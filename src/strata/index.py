"""The index: sync(records), search(), read() (design.md "Index").

One disposable SQLite file, ``.strata/cache/index.db``, derived from whatever
Records were last handed to :meth:`Index.sync`. Durable paragraph identity -
which anchor a source paragraph carries, and its exact retired text - lives
apart in :class:`strata.ledger.Ledger` (ADR-0001); this module treats the
ledger as an already-current, read-only collaborator for anchor lookups and
retired/live text, never as something it registers or aligns itself. Refs
are parsed and rendered through :mod:`strata.refs` only.

Three responsibilities behind two methods, plus freshness:

- ``sync(records)`` diffs by whole-record content hash, so an unchanged
  record costs nothing; a per-paragraph embedding cache (keyed by exact
  paragraph text, CONTEXT.md "Match key") means an unchanged paragraph is
  never re-embedded even inside a changed record, and a changed embedder
  identity invalidates the whole cache.
- ``search(...)`` returns a :class:`SearchReply`: a header (index/corpus
  revision, lexical and evidence totals, token estimate, month/coverage/chunk
  breakdowns) followed by hits, hybrid-ranked by reciprocal rank fusion
  (k=60, equal weights) over lexical (FTS5, unlimited) and semantic
  (sqlite-vec, top 200) paragraph matches under identical filters, one hit
  per record at its best-scoring paragraph.
- ``read(ref, cursor)`` returns verbatim text for any ref shape as a
  :class:`ReadReply`: transport labels (the ref line, warning and
  retired-anchor markers, a citation per source paragraph) kept apart from
  the exact payload, paged at paragraph boundaries or, inside an oversized
  paragraph, at Unicode character boundaries.

Enumeration is simplified relative to a web-scale design: at this project's
target size (a personal corpus, python-stack.md), recomputing a query's full
sorted evidence list on every call and slicing it in Python is fast and,
importantly, easy to keep exactly deduplicated and stably ordered - the
property the acceptance gate actually requires. A single opaque cursor per
reply resumes every unfinished list (hits, by_month, covered, chunks)
together, rather than issuing one cursor per list; CONTEXT.md's "a cursor
alone resumes exact scope" is read as one cursor per reply, not per field.
An assignment cursor (a ``chunks`` row's) is the same shape plus a compact
scope - the first and last plan key of its slice - and executes exactly
that slice of the plan, recomputed at the same revision.
"""

from __future__ import annotations

import base64
import hashlib
import json
import math
import os
import sqlite3
import unicodedata
from dataclasses import dataclass, replace
from datetime import date as _date, timedelta
from pathlib import Path, PurePosixPath

from strata import refs
from strata.embeddings import Embedder
from strata.ledger import Ledger
from strata.record import Date, Record, cap_at_word_boundary

REPLY_TOKEN_BUDGET = 8_000
QUERY_HIT_CAP = 100
BROWSE_HIT_CAP = 500
SEMANTIC_K = 200
RRF_K = 60
DEFAULT_CHUNK_TOKENS = 80_000
LABEL_WIDTH = 16


class CursorError(ValueError):
    """A cursor was invalid, invalidated, or combined with conflicting
    arguments. The message names the original scope so the caller can
    restart it (CONTEXT.md: continuation cursor)."""


class BadRef(ValueError):
    """``read`` was given a ref shape it cannot resolve."""


class SemanticUnavailable(RuntimeError):
    """``semantic=True`` but sqlite-vec cannot be used: either the module is
    not installed, or this Python's ``sqlite3`` cannot load extensions.
    Raised at construction rather than degrading silently to lexical-only
    (``semantic=False`` stays the explicit, caller-chosen way to get that)."""


# -- token estimate, dates, periods -----------------------------------------


def estimate_tokens(text: str) -> int:
    """``ceil(utf-8 bytes / 4)`` (#10's resolution): no tokenizer."""
    return math.ceil(len(text.encode("utf-8")) / 4) if text else 0


def _month_key(iso: str) -> str:
    return iso[:7]


def _last_day_of_month(year: int, month: int) -> str:
    if month == 12:
        nxt = _date(year + 1, 1, 1)
    else:
        nxt = _date(year, month + 1, 1)
    return (nxt - timedelta(days=1)).isoformat()


def _period_spec_bounds(spec: str) -> tuple[str, str]:
    """``YYYY`` | ``YYYY-MM`` | ``YYYY-MM-DD`` -> inclusive ISO-day bounds."""
    if len(spec) == 4:
        return f"{spec}-01-01", f"{spec}-12-31"
    if len(spec) == 7:
        year, month = int(spec[:4]), int(spec[5:7])
        return f"{spec}-01", _last_day_of_month(year, month)
    return spec, spec


def _period_bounds_of(date: Date) -> tuple[str, str] | None:
    """The record's own period as inclusive ISO-day bounds, or ``None`` for
    ``unknown`` (a hint, never a gate: it always passes a range filter)."""
    if date.confidence == "unknown":
        return None
    if date.granularity == "day":
        return date.iso, date.iso
    if date.granularity == "month":
        year, month = int(date.iso[:4]), int(date.iso[5:7])
        return f"{date.iso[:7]}-01", _last_day_of_month(year, month)
    year = int(date.iso[:4])
    return f"{year:04d}-01-01", f"{year:04d}-12-31"


UNKNOWN_PERIOD = (0, 99_999_999)


def _period_ints(date: Date) -> tuple[int, int]:
    """The record's period as ``YYYYMMDD`` integers for the vec0 metadata
    columns; ``unknown`` spans everything so it passes every range, the
    same rule :func:`passes_range` applies on the lexical side."""
    bounds = _period_bounds_of(date)
    if bounds is None:
        return UNKNOWN_PERIOD
    return int(bounds[0].replace("-", "")), int(bounds[1].replace("-", ""))


def _range_ints(from_: str | None, to: str | None) -> tuple[int, int]:
    start = _period_spec_bounds(from_)[0] if from_ else "0000-01-01"
    end = _period_spec_bounds(to)[1] if to else "9999-12-31"
    return int(start.replace("-", "")), int(end.replace("-", ""))


def passes_range(date: Date, from_: str | None, to: str | None) -> bool:
    """A record is dropped only when its period lies entirely outside the
    range; ``unknown`` always passes (design.md, dates are a hint)."""
    if date.confidence == "unknown" or (from_ is None and to is None):
        return True
    bounds = _period_bounds_of(date)
    start, end = bounds
    range_start = _period_spec_bounds(from_)[0] if from_ else "0000-01-01"
    range_end = _period_spec_bounds(to)[1] if to else "9999-12-31"
    return start <= range_end and end >= range_start


def display_date(date: Date) -> str:
    """``exact`` -> the ISO day. ``inferred`` -> the period at its own
    granularity plus the origin wording, never a rounded day
    (``2013-11 (folder)``). ``unknown`` -> ``undated`` (#10 point 6, 9)."""
    if date.confidence == "unknown":
        return "undated"
    if date.confidence == "exact":
        return date.iso
    if date.granularity == "day":
        period = date.iso
    elif date.granularity == "month":
        period = date.iso[:7]
    else:
        period = date.iso[:4]
    return f"{period} ({date.text})" if date.text else period


def windows_overlap(a: tuple[str, str], b_from: str | None, b_to: str | None) -> bool:
    start = b_from or "0000-01-01"
    end = b_to or "9999-12-31"
    return a[0] <= end and a[1] >= start


# -- cursors ------------------------------------------------------------------


def _encode_cursor(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _fts_phrase(text: str) -> str:
    return '"' + text.replace('"', '""') + '"'


def _fts_expression(query: str) -> str:
    """A query's lexical branch expression (#46): terms combined with AND,
    double quotes mark a phrase, an unbalanced quote runs to the end of the
    query. Each bare term and each quoted run becomes one ``_fts_phrase``, so
    FTS5 syntax characters (``e-mail``, ``gas*``, ``NOT``, ``(``) are matched
    literally and never raise. A query of only quotes or whitespace parses to
    ``""`` - the caller's empty-query browse case. ``who`` stays exact-phrase
    via ``_fts_phrase`` directly; this function is not used there."""
    phrases = []
    i, n = 0, len(query)
    while i < n:
        ch = query[i]
        if ch.isspace():
            i += 1
            continue
        if ch == '"':
            end = query.find('"', i + 1)
            content, i = (query[i + 1 : end], end + 1) if end != -1 else (query[i + 1 :], n)
        else:
            j = i
            while j < n and not query[j].isspace() and query[j] != '"':
                j += 1
            content, i = query[i:j], j
        if content.strip():
            phrases.append(_fts_phrase(content))
    return " AND ".join(phrases)


def _browse_key(row: sqlite3.Row) -> tuple:
    """Browse order: ``iso`` ascending, then ref, unknown last (#10 ss9)."""
    return (row["confidence"] == "unknown", row["iso"], row["ref"])


@dataclass(frozen=True)
class _HitSpec:
    ref: str
    best_paragraph_id: int | None
    matches: int
    score: float
    is_lexical: bool
    lexical_matches: int = 0
    paragraphs: int = 1


@dataclass(frozen=True)
class _Assignment:
    """One entry of a chunk plan: contiguous records in plan order, their
    estimated cost, and ``segment=(k, n)`` for the k-th bounded read segment
    of one oversized record."""

    refs: list[str]
    tokens: int
    segment: tuple[int, int] | None = None


def _plan_key(row: sqlite3.Row) -> list:
    """Plan order (#10 ss4): date, then ref, unknown last; a partial date
    sorts by its period start. A JSON-friendly list, because an assignment
    cursor carries its first and last key as its compact scope."""
    return [int(row["confidence"] == "unknown"), row["iso"], row["ref"]]


@dataclass(frozen=True)
class _QueryEvidence:
    specs: list[_HitSpec]
    paragraph_rows: dict[int, sqlite3.Row]


def _safe_char_cut(text: str, max_bytes: int) -> int:
    """The largest character index ``k`` with ``text[:k]`` encoding to at
    most ``max_bytes`` UTF-8 bytes, backed off so it never separates a base
    character from a following combining mark (#10 point 10: Unicode
    character boundaries, never mid-codepoint or mid-grapheme)."""
    total = 0
    cut = 0
    for index, char in enumerate(text):
        char_len = len(char.encode("utf-8"))
        if total + char_len > max_bytes:
            break
        total += char_len
        cut = index + 1
    while 0 < cut < len(text) and _joins_previous(text, cut):
        cut -= 1
    return cut


def _joins_previous(text: str, index: int) -> bool:
    """``text[index]`` may not be separated from ``text[index - 1]``: a
    combining mark, or the ``\\n`` of a CRLF pair."""
    char = text[index]
    return unicodedata.combining(char) != 0 or (char == "\n" and text[index - 1] == "\r")


def _minimum_cut(text: str) -> int:
    """The shortest non-empty prefix that ends on a safe boundary."""
    cut = 1
    while cut < len(text) and _joins_previous(text, cut):
        cut += 1
    return cut


PARAGRAPH_SEPARATOR = "\n\n"


@dataclass(frozen=True)
class ReadPiece:
    """One paragraph, or the fragment of one, on a read page. ``label`` is a
    transport label (a source paragraph's citation), never payload; ``text``
    and ``separator`` are exact stored characters. The separator that
    follows a paragraph belongs to the page that finishes the paragraph, so
    the pages' payloads concatenate to the selection's exact stored text
    (design.md "Read")."""

    label: str | None
    text: str
    separator: str

    def payload(self) -> str:
        return self.text + self.separator


def _paginate(
    units: list[tuple[str | None, str]], start_para: int, start_char: int, budget_bytes: int
) -> tuple[list[ReadPiece], dict | None]:
    """Walk ``units`` (``(label, paragraph)`` pairs) from ``(start_para,
    start_char)``, filling ``budget_bytes`` with labels, text and owned
    separators. A page breaks at a paragraph boundary whenever it can; only
    a paragraph that alone exceeds a whole page is cut, at a safe character
    boundary, without its separator, and resumed exactly where it left off
    next time (design.md "Read")."""
    pieces: list[ReadPiece] = []
    total = 0
    para, char = start_para, start_char
    while para < len(units):
        label, text = units[para]
        if char and label:
            label = f"{label} (continued)"
        remaining = text[char:]
        separator = PARAGRAPH_SEPARATOR if para < len(units) - 1 else ""
        label_bytes = len(label.encode("utf-8")) + 1 if label else 0
        needed = label_bytes + len(remaining.encode("utf-8")) + len(separator)
        if total + needed <= budget_bytes:
            pieces.append(ReadPiece(label, remaining, separator))
            total += needed
            para += 1
            char = 0
            continue
        if pieces:
            break  # the next paragraph starts a page of its own
        room = budget_bytes - label_bytes
        cut = _safe_char_cut(remaining, room) if room > 0 else 0
        if cut <= 0:
            cut = _minimum_cut(remaining)
            if len(remaining[:cut].encode("utf-8")) > max(0, room):
                cut = len(remaining.encode("utf-8")[:max(0, room)].decode("utf-8", errors="ignore"))
            if not cut:
                raise ValueError("read paragraph label exceeds the reply budget")
        pieces.append(ReadPiece(label, remaining[:cut], ""))
        return pieces, {"para": para, "char": char + cut}
    if para >= len(units):
        return pieces, None
    return pieces, {"para": para, "char": char}


def _heading_depth(paragraph: str) -> int | None:
    stripped = paragraph.lstrip()
    depth = len(stripped) - len(stripped.lstrip("#"))
    if depth == 0 or not stripped[depth:].startswith(" "):
        return None
    return depth


def _heading_text(paragraph: str, depth: int) -> str:
    return paragraph.lstrip()[depth:].strip()


def _manuscript_section(paragraphs: tuple[str, ...], heading: str, occurrence: int) -> list[str]:
    """The paragraphs of one heading section: the heading paragraph itself
    up to, but not including, the next heading at the same or a shallower
    depth (a subsection stays inside its parent)."""
    matches = [
        index
        for index, paragraph in enumerate(paragraphs)
        if (depth := _heading_depth(paragraph)) is not None and _heading_text(paragraph, depth) == heading
    ]
    if occurrence > len(matches):
        raise BadRef(f"heading {heading!r} does not occur {occurrence} time(s)")
    start = matches[occurrence - 1]
    depth = _heading_depth(paragraphs[start])
    end = len(paragraphs)
    for index in range(start + 1, len(paragraphs)):
        other_depth = _heading_depth(paragraphs[index])
        if other_depth is not None and other_depth <= depth:
            end = index
            break
    return list(paragraphs[start:end])


def _decode_cursor(cursor: str) -> dict:
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded.encode("ascii"))
        return json.loads(raw)
    except Exception as error:
        raise CursorError(f"not a valid cursor: {cursor!r}") from error


# -- public result shapes ------------------------------------------------------


@dataclass(frozen=True)
class Hit:
    ref: str
    kind: str
    date: Date
    title: str
    matches: int
    snippet: str = ""

    def line(self) -> str:
        # The title is capped on display only: the stored title, title_fts
        # and the who expansion keep the full title (issue #42).
        title = cap_at_word_boundary(self.title)
        head = f"{self.ref}  {display_date(self.date)}  {self.kind}  {title}"
        if self.matches > 1:
            head += f"  ({self.matches} matches)"
        return head if not self.snippet else f"{head}\n    {self.snippet}"


@dataclass(frozen=True)
class MonthRow:
    month: str
    records: int
    tokens: int

    def line(self) -> str:
        return f"{self.month}  {self.records} records  ~{self.tokens}"


@dataclass(frozen=True)
class CoveredRow:
    ref: str
    window: tuple[str, str]

    def line(self) -> str:
        return f"{self.ref}  {self.window[0]}..{self.window[1]}"


@dataclass(frozen=True)
class ChunkRow:
    """One executable reader assignment (CONTEXT.md "Chunk"): its scope,
    record count, estimated cost and cursor. ``segment`` is ``(k, n)`` when
    the assignment is the k-th of n bounded read segments of one oversized
    record; such a record is read only once every segment is."""

    from_: str
    to: str
    records: int
    tokens: int
    cursor: str
    segment: tuple[int, int] | None = None

    def line(self) -> str:
        head = f"{self.from_}..{self.to}  {self.records} records  ~{self.tokens}"
        if self.segment is not None:
            head += f"  segment {self.segment[0]} of {self.segment[1]}"
        return f"{head}  {self.cursor}"


@dataclass(frozen=True)
class SearchReply:
    indexing: str
    index_revision: str
    corpus_revision: str
    lexical_records: int
    lexical_paragraphs: int
    evidence_records: int
    evidence_paragraphs: int
    tokens: int
    by_month: list[MonthRow]
    undated: int
    inferred: int
    covered: list[CoveredRow]
    chunks: list[ChunkRow]
    hits: list[Hit]
    shown: int
    total_shown_of: int
    continuation: str | None
    reply_tokens: int = 0

    def text(self) -> str:
        return _render_reply(self)


@dataclass(frozen=True)
class ReadReply:
    """``labels`` are the transport lines ahead of the text: the selection's
    ref, date, kind and title, then any warning or retired-anchor markers.
    ``pieces`` carry this page's payload; ``body`` is that payload alone,
    exact stored characters and nothing else."""

    ref: str
    labels: tuple[str, ...]
    pieces: tuple[ReadPiece, ...]
    continuation: str | None
    reply_tokens: int = 0
    metadata: str | None = None

    @property
    def body(self) -> str:
        return "".join(piece.payload() for piece in self.pieces)

    def text(self) -> str:
        stream = "".join((f"{piece.label}\n" if piece.label else "") + piece.payload() for piece in self.pieces)
        header = self.metadata if self.metadata is not None else ("\n".join(self.labels) + "\n\n" if self.labels else "")
        if self.metadata:
            header = "[metadata fragment]\n" + header + "\n[end metadata fragment]\n"
        out = header + stream
        if self.continuation:
            if not out.endswith("\n"):
                out += "\n"
            out += f"[continues: {self.continuation}]"
        return f"{out}\nreply_tokens ~{self.reply_tokens}"


def _with_read_reply_tokens(reply: ReadReply) -> ReadReply:
    for _ in range(4):
        count = estimate_tokens(reply.text())
        if count == reply.reply_tokens:
            break
        reply = replace(reply, reply_tokens=count)
    return reply


def _field_line(label: str, value: str) -> str:
    return f"{label.ljust(LABEL_WIDTH)}{value}"


def _list_field(label: str, rows: list[str], empty: str) -> str:
    if not rows:
        return _field_line(label, empty)
    lines = [_field_line(label, rows[0])]
    indent = " " * LABEL_WIDTH
    lines += [f"{indent}{row}" for row in rows[1:]]
    return "\n".join(lines)


def _with_reply_tokens(reply: SearchReply) -> SearchReply:
    """``reply_tokens`` estimates the reply's own serialized size (#10 point
    2), so it is filled in from a first render and then fixed once - the
    estimate does not chase its own tail."""
    draft = replace(reply, reply_tokens=0)
    return replace(draft, reply_tokens=estimate_tokens(_render_reply(draft)))


def _render_reply(reply: SearchReply) -> str:
    lines = [
        _field_line("indexing", reply.indexing),
        _field_line("index_revision", reply.index_revision),
        _field_line("corpus_revision", reply.corpus_revision),
        _field_line("lexical_total", f"{reply.lexical_records} records, {reply.lexical_paragraphs} paragraphs"),
        _field_line("evidence_total", f"{reply.evidence_records} records, {reply.evidence_paragraphs} paragraphs"),
        _field_line("tokens", f"~{reply.tokens}"),
        _list_field("by_month", [row.line() for row in reply.by_month], "none"),
        _field_line("undated", str(reply.undated)),
        _field_line("inferred", str(reply.inferred)),
        _list_field("covered", [row.line() for row in reply.covered], "none"),
        _list_field("chunks", [row.line() for row in reply.chunks], "none"),
        _field_line("shown", f"{reply.shown} of {reply.total_shown_of} evidence records"),
        _field_line("continuations", reply.continuation or "none"),
        _field_line("reply_tokens", f"~{reply.reply_tokens}"),
    ]
    body = "\n".join(lines)
    if reply.hits:
        body += "\n\n" + "\n".join(hit.line() for hit in reply.hits)
    return body


# -- schema --------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS records (
    ref TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    type TEXT,
    iso TEXT NOT NULL,
    confidence TEXT NOT NULL,
    granularity TEXT NOT NULL,
    date_text TEXT NOT NULL,
    title TEXT NOT NULL,
    aliases_json TEXT NOT NULL,
    window_from TEXT,
    window_to TEXT,
    corpus_revision TEXT,
    coverage_complete INTEGER,
    warnings_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    tokens INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS paragraphs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ref TEXT NOT NULL,
    idx INTEGER NOT NULL,
    anchor INTEGER,
    text TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    UNIQUE (ref, idx)
);
CREATE INDEX IF NOT EXISTS paragraphs_ref ON paragraphs(ref);
CREATE INDEX IF NOT EXISTS paragraphs_hash ON paragraphs(content_hash);
CREATE VIRTUAL TABLE IF NOT EXISTS paragraph_fts USING fts5(
    text, content='paragraphs', content_rowid='id', tokenize='unicode61'
);
CREATE VIRTUAL TABLE IF NOT EXISTS title_fts USING fts5(
    ref UNINDEXED, title, tokenize='unicode61'
);
CREATE TABLE IF NOT EXISTS embedding_cache (content_hash TEXT PRIMARY KEY, vector BLOB NOT NULL);
CREATE TEMP TABLE IF NOT EXISTS who_refs (ref TEXT PRIMARY KEY);
"""

# Bumped whenever paragraph_vec's shape changes; stored beside the embedder
# identity so an older cache file rebuilds its vectors instead of failing.
_VEC_SCHEMA = "2"
SQL_BATCH = 500  # well under every SQLite build's bound-variable limit


def _hash_text(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


def _record_content_hash(record: Record) -> str:
    return _hash_text(
        record.kind,
        record.type or "",
        record.date.iso,
        record.date.confidence,
        record.date.granularity,
        record.date.text,
        record.title,
        json.dumps(record.aliases),
        json.dumps(record.window),
        record.corpus_revision or "",
        "" if record.coverage_complete is None else str(record.coverage_complete),
        json.dumps(record.warnings),
        "\x1f".join(record.paragraphs),
    )


def _pack_vector(vector: tuple[float, ...]) -> bytes:
    import struct

    return struct.pack(f"<{len(vector)}f", *vector)


def _unpack_vector(blob: bytes) -> tuple[float, ...]:
    import struct

    n = len(blob) // 4
    return struct.unpack(f"<{n}f", blob)


class Index:
    """``.strata/cache/index.db``. Disposable: delete the file and construct
    a fresh :class:`Index` over the same records to rebuild (design.md)."""

    def __init__(
        self,
        path: str | Path,
        *,
        ledger: Ledger,
        embedder: Embedder,
        chunk_tokens: int = DEFAULT_CHUNK_TOKENS,
        semantic: bool = True,
        reply_token_budget: int = REPLY_TOKEN_BUDGET,
    ):
        path = Path(path)
        is_new = not path.exists()
        self._ledger = ledger
        self._embedder = embedder
        self.chunk_tokens = chunk_tokens
        if type(chunk_tokens) is not int or chunk_tokens <= 0:
            raise ValueError("chunk_tokens must be a positive integer")
        if not 2000 <= reply_token_budget <= REPLY_TOKEN_BUDGET:
            raise ValueError("reply_token_budget must be between 2000 and 8000")
        self.reply_token_budget = reply_token_budget
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.executescript(_SCHEMA)
            self._conn.execute("BEGIN")
            # `semantic=False` is the caller's own choice of lexical-only, useful
            # to a caller and to tests isolating lexical behaviour from the
            # always-on top-200 semantic branch. `semantic=True` with no usable
            # sqlite-vec is not the same thing: `_load_vec` raises rather than
            # degrading silently (issue #42).
            self._vec_ok = self._load_vec() if semantic else False
            if is_new or self._get_meta("epoch") is None:
                self._set_meta("epoch", os.urandom(8).hex())
                self._set_meta("revision", "0")
            self._ensure_embedder_identity()
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            self._conn.close()
            raise

    def close(self) -> None:
        self._conn.close()

    # -- vec0 setup ---------------------------------------------------------

    def _load_vec(self) -> bool:
        try:
            import sqlite_vec
        except ImportError as error:
            raise SemanticUnavailable(
                "semantic=True needs sqlite-vec, which is not installed "
                "(pass semantic=False for lexical-only)"
            ) from error
        try:
            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            self._conn.enable_load_extension(False)
        except (AttributeError, sqlite3.NotSupportedError, sqlite3.OperationalError) as error:
            raise SemanticUnavailable(
                "semantic=True needs sqlite-vec, but this Python's sqlite3 "
                f"could not load it as an extension ({error})"
            ) from error
        return True

    def _vec_table_ready(self) -> bool:
        if not self._vec_ok:
            return False
        row = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'paragraph_vec'"
        ).fetchone()
        return row is not None

    def _ensure_embedder_identity(self) -> None:
        stored = (self._get_meta("embedder_model"), self._get_meta("embedder_dim"), self._get_meta("vec_schema"))
        current = (self._embedder.model_id, str(self._embedder.dim), _VEC_SCHEMA)
        if stored == current:
            return
        # A model change invalidates every cached vector; re-embed whatever
        # is already indexed right away, rather than waiting for the next
        # sync() to notice a "changed" record it will not see as changed
        # (design.md ss "Search": an embedder-identity row forces this).
        self._conn.execute("DROP TABLE IF EXISTS paragraph_vec")
        self._conn.execute("DELETE FROM embedding_cache")
        if self._vec_ok:
            # kind and the period as vec0 metadata columns, so the semantic
            # branch applies the same filters as the lexical one inside the
            # KNN rather than through a bounded IN list (python-stack.md).
            self._conn.execute(
                f"CREATE VIRTUAL TABLE paragraph_vec USING vec0("
                f"paragraph_id INTEGER PRIMARY KEY, embedding FLOAT[{self._embedder.dim}], "
                f"kind TEXT, period_start INTEGER, period_end INTEGER)"
            )
        for key, value in zip(("embedder_model", "embedder_dim", "vec_schema"), current):
            self._set_meta(key, value)
        existing = self._conn.execute(
            "SELECT p.id AS id, p.text AS text, p.content_hash AS content_hash, r.kind AS kind, "
            "r.iso AS iso, r.confidence AS confidence, r.granularity AS granularity, r.date_text AS date_text "
            "FROM paragraphs p JOIN records r ON r.ref = p.ref"
        ).fetchall()
        if existing:
            new_vectors = [
                (row["id"], row["content_hash"], row["kind"], *_period_ints(self._record_date(row))) for row in existing
            ]
            text_of_hash = {row["content_hash"]: row["text"] for row in existing}
            self._embed_new(new_vectors, text_of_hash)
        self._bump_revision()

    # -- meta / revision ------------------------------------------------------

    def _get_meta(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self._conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    def _bump_revision(self) -> None:
        counter = int(self._get_meta("revision") or "0") + 1
        self._set_meta("revision", str(counter))

    @property
    def index_revision(self) -> str:
        return f"{self._get_meta('epoch')}-{self._get_meta('revision')}"

    def mark_complete(self, complete: bool = True, *, indexed: int | None = None) -> None:
        """``complete``, or ``incomplete`` with progress (the records indexed
        so far) so a caller can refuse completeness claims during a first
        index (design.md "Index")."""
        if indexed is None:
            indexed = self._conn.execute("SELECT COUNT(*) AS n FROM records").fetchone()["n"]
        self._set_meta("indexing", "complete" if complete else f"incomplete ({indexed} records indexed)")
        self._conn.commit()

    @property
    def indexing_state(self) -> str:
        return self._get_meta("indexing") or "incomplete (0 records indexed)"

    # -- sync -----------------------------------------------------------------

    def sync(self, records: list[Record], *, complete: bool = True) -> dict:
        """Publish a complete snapshot, or roll back and mark refresh failed."""
        try:
            with self._conn:
                return self._sync(records, complete=complete)
        except BaseException:
            self._conn.rollback()
            self.mark_complete(False)
            raise

    def _sync(self, records: list[Record], *, complete: bool = True) -> dict:
        """Diff by whole-record content hash: an unchanged record is
        untouched; a changed or new one is fully replaced; a vanished one is
        deleted. Paragraph embeddings are cached by exact text
        (CONTEXT.md: match key), so an unchanged paragraph never re-embeds
        even inside a changed record."""
        existing = {
            row["ref"]: row["content_hash"] for row in self._conn.execute("SELECT ref, content_hash FROM records")
        }
        incoming = {record.ref: record for record in records}
        changed_any = self._get_meta("corpus_revision") != self._ledger.corpus_revision()

        for ref in existing.keys() - incoming.keys():
            self._delete_record(ref)
            changed_any = True

        for ref, record in incoming.items():
            content_hash = _record_content_hash(record)
            if existing.get(ref) == content_hash:
                continue
            if ref in existing:
                self._delete_record(ref)
            self._insert_record(record, content_hash)
            changed_any = True

        if changed_any:
            self._bump_revision()
        self._set_meta("corpus_revision", self._ledger.corpus_revision())
        self.mark_complete(complete)
        self._conn.commit()
        return {"changed": changed_any, "records": len(incoming)}

    def _delete_record(self, ref: str) -> None:
        ids = [row["id"] for row in self._conn.execute("SELECT id FROM paragraphs WHERE ref = ?", (ref,))]
        for pid in ids:
            self._conn.execute("DELETE FROM paragraph_fts WHERE rowid = ?", (pid,))
            if self._vec_table_ready():
                self._conn.execute("DELETE FROM paragraph_vec WHERE paragraph_id = ?", (pid,))
        self._conn.execute("DELETE FROM paragraphs WHERE ref = ?", (ref,))
        self._conn.execute("DELETE FROM title_fts WHERE ref = ?", (ref,))
        self._conn.execute("DELETE FROM records WHERE ref = ?", (ref,))

    def _insert_record(self, record: Record, content_hash: str) -> None:
        whole_text = "\n\n".join(record.paragraphs)
        window_from, window_to = record.window if record.window else (None, None)
        self._conn.execute(
            "INSERT INTO records (ref, kind, type, iso, confidence, granularity, date_text, title, "
            "aliases_json, window_from, window_to, corpus_revision, coverage_complete, warnings_json, "
            "content_hash, tokens) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                record.ref,
                record.kind,
                record.type,
                record.date.iso,
                record.date.confidence,
                record.date.granularity,
                record.date.text,
                record.title,
                json.dumps(record.aliases),
                window_from,
                window_to,
                record.corpus_revision,
                None if record.coverage_complete is None else int(record.coverage_complete),
                json.dumps(record.warnings),
                content_hash,
                estimate_tokens(whole_text),
            ),
        )
        self._conn.execute("INSERT INTO title_fts (ref, title) VALUES (?, ?)", (record.ref, record.title))
        anchors = self._ledger.live_anchors(record.ref) if record.kind == "source" else []
        if record.kind == "source" and len(anchors) != len(record.paragraphs):
            # The ledger is an already-current collaborator: a sources
            # adapter that forgot align() fails here, loudly, rather than
            # indexing paragraphs that cite nothing (ADR-0001).
            raise ValueError(
                f"{record.ref}: the ledger holds {len(anchors)} live anchors for {len(record.paragraphs)} "
                f"paragraphs; align() must run before sync()"
            )
        period_start, period_end = _period_ints(record.date)
        new_vectors: list[tuple[int, str, str, int, int]] = []
        text_of_hash: dict[str, str] = {}
        for idx, text in enumerate(record.paragraphs):
            p_hash = _hash_text(text)
            anchor = anchors[idx] if idx < len(anchors) else None
            cur = self._conn.execute(
                "INSERT INTO paragraphs (ref, idx, anchor, text, content_hash) VALUES (?,?,?,?,?)",
                (record.ref, idx, anchor, text, p_hash),
            )
            pid = cur.lastrowid
            self._conn.execute("INSERT INTO paragraph_fts (rowid, text) VALUES (?, ?)", (pid, text))
            new_vectors.append((pid, p_hash, record.kind, period_start, period_end))
            text_of_hash[p_hash] = text
        self._embed_new(new_vectors, text_of_hash)

    def _embed_new(self, new_vectors: list[tuple[int, str, str, int, int]], text_of_hash: dict[str, str]) -> None:
        """``new_vectors`` rows are ``(paragraph_id, content_hash, kind,
        period_start, period_end)``; the last three land in the vec0
        metadata columns beside the embedding."""
        if not new_vectors or not self._vec_ok:
            return
        to_embed: list[str] = []
        to_embed_hashes: list[str] = []
        cached: dict[str, tuple[float, ...]] = {}
        for _, p_hash, *_meta in new_vectors:
            if p_hash in cached:
                continue
            row = self._conn.execute("SELECT vector FROM embedding_cache WHERE content_hash = ?", (p_hash,)).fetchone()
            if row is not None:
                cached[p_hash] = _unpack_vector(row["vector"])
            elif p_hash not in to_embed_hashes:
                to_embed_hashes.append(p_hash)
                to_embed.append(text_of_hash[p_hash])
        if to_embed:
            fresh = self._embedder.embed_passages(to_embed)
            for p_hash, vector in zip(to_embed_hashes, fresh):
                cached[p_hash] = vector
                self._conn.execute(
                    "INSERT OR REPLACE INTO embedding_cache (content_hash, vector) VALUES (?, ?)",
                    (p_hash, _pack_vector(vector)),
                )
        if self._vec_table_ready():
            for pid, p_hash, kind, period_start, period_end in new_vectors:
                self._conn.execute(
                    "INSERT INTO paragraph_vec (paragraph_id, embedding, kind, period_start, period_end) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (pid, _pack_vector(cached[p_hash]), kind, period_start, period_end),
                )

    @property
    def embedder_calls(self) -> int:
        """Test hook: number of distinct texts ever sent to the embedder
        this session (the fake embedder's own counter is the real source of
        truth; this just exposes the cache size)."""
        return self._conn.execute("SELECT COUNT(*) AS n FROM embedding_cache").fetchone()["n"]

    # -- who: alias expansion ------------------------------------------------

    def _alias_set(self, who: str) -> set[str]:
        """CONTEXT.md "Alias": the string, expanded case-insensitively
        through every note whose title, filename stem or aliases equal it,
        unioned into one set from every matching note. No match -> the
        string alone (#10 resolution ss7)."""
        aliases: set[str] = set()
        matched_any = False
        for row in self._conn.execute("SELECT ref, title, aliases_json FROM records WHERE kind = 'note'"):
            stem = PurePosixPath(row["ref"]).stem
            candidates = {row["title"], stem, *json.loads(row["aliases_json"])}
            if any(candidate.lower() == who.lower() for candidate in candidates if candidate):
                aliases |= {candidate for candidate in candidates if candidate}
                matched_any = True
        return aliases if matched_any else {who}

    def _alias_matching_refs(self, aliases: set[str]) -> set[str]:
        """Every record whose title or paragraphs carry any alias as an FTS
        phrase - one predicate, two placements is the SQL-branch version of
        this same idea (python-stack.md); here it is one Python set used by
        both."""
        phrase_query = " OR ".join(_fts_phrase(alias) for alias in aliases)
        matching: set[str] = set()
        for row in self._conn.execute("SELECT ref FROM title_fts WHERE title_fts MATCH ?", (phrase_query,)):
            matching.add(row["ref"])
        for row in self._conn.execute(
            "SELECT DISTINCT p.ref AS ref FROM paragraph_fts "
            "JOIN paragraphs p ON p.id = paragraph_fts.rowid WHERE paragraph_fts MATCH ?",
            (phrase_query,),
        ):
            matching.add(row["ref"])
        return matching

    # -- filters --------------------------------------------------------------

    def _all_record_rows(self) -> list[sqlite3.Row]:
        return self._conn.execute("SELECT * FROM records").fetchall()

    def _eligible_refs(
        self, *, from_: str | None, to: str | None, who_matches: set[str] | None, kind: str | None
    ) -> set[str]:
        eligible: set[str] = set()
        for row in self._all_record_rows():
            if kind and row["kind"] != kind:
                continue
            date = Date(
                iso=row["iso"], confidence=row["confidence"], granularity=row["granularity"], text=row["date_text"]
            )
            if not passes_range(date, from_, to):
                continue
            if who_matches is not None and row["ref"] not in who_matches:
                continue
            eligible.add(row["ref"])
        return eligible

    # -- hybrid query evidence -------------------------------------------------

    def _lexical_paragraph_rows(self, query: str) -> list[sqlite3.Row]:
        expression = _fts_expression(query)
        if not expression:
            return []
        return self._conn.execute(
            "SELECT p.id AS id, p.ref AS ref, p.idx AS idx FROM paragraph_fts "
            "JOIN paragraphs p ON p.id = paragraph_fts.rowid "
            "WHERE paragraph_fts MATCH ? ORDER BY bm25(paragraph_fts), p.ref, p.idx",
            (expression,),
        ).fetchall()

    def _semantic_paragraph_ids(
        self,
        query: str,
        *,
        from_: str | None,
        to: str | None,
        kind: str | None,
        who_matches: set[str] | None,
    ) -> list[int]:
        """The top ``SEMANTIC_K`` paragraphs under the same predicate the
        lexical branch uses: ``kind`` and period overlap on the vec0
        metadata columns, ``who`` as a rowid set (#10 ss7: one predicate,
        two placements). Nothing here grows with the corpus, so a corpus
        past SQLite's bound-variable limit is no different from a small one."""
        if not self._vec_table_ready():
            return []
        sql = "SELECT paragraph_id, distance FROM paragraph_vec WHERE embedding MATCH ? AND k = ?"
        params: list = [_pack_vector(self._embedder.embed_query(query)), SEMANTIC_K]
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        if from_ or to:
            range_start, range_end = _range_ints(from_, to)
            sql += " AND period_start <= ? AND period_end >= ?"
            params += [range_end, range_start]
        if who_matches is not None:
            self._conn.execute("DELETE FROM who_refs")
            self._conn.executemany("INSERT INTO who_refs (ref) VALUES (?)", [(ref,) for ref in who_matches])
            sql += " AND paragraph_id IN (SELECT id FROM paragraphs WHERE ref IN (SELECT ref FROM who_refs))"
        rows = self._conn.execute(sql + " ORDER BY distance", params).fetchall()
        # vec0 orders by distance alone; ties settle on paragraph id so a
        # cursor re-running this query sees the same order.
        ordered = sorted(rows, key=lambda row: (row["distance"], row["paragraph_id"]))
        return [row["paragraph_id"] for row in ordered]

    def _query_evidence(
        self,
        query: str,
        eligible: set[str],
        *,
        from_: str | None,
        to: str | None,
        kind: str | None,
        who_matches: set[str] | None,
    ) -> "_QueryEvidence":
        all_lexical = [row for row in self._lexical_paragraph_rows(query) if row["ref"] in eligible]
        lexical_ids = [row["id"] for row in all_lexical]
        semantic_ids = self._semantic_paragraph_ids(query, from_=from_, to=to, kind=kind, who_matches=who_matches)

        score: dict[int, float] = {}
        for rank, pid in enumerate(lexical_ids, start=1):
            score[pid] = score.get(pid, 0.0) + 1.0 / (RRF_K + rank)
        for rank, pid in enumerate(semantic_ids, start=1):
            score[pid] = score.get(pid, 0.0) + 1.0 / (RRF_K + rank)

        evidence_ids = set(lexical_ids) | set(semantic_ids)
        paragraph_rows = self._paragraphs_by_id(evidence_ids)
        lexical_set = set(lexical_ids)

        per_ref: dict[str, list[int]] = {}
        for pid in evidence_ids:
            per_ref.setdefault(paragraph_rows[pid]["ref"], []).append(pid)

        specs: list[_HitSpec] = []
        for ref, pids in per_ref.items():
            best = max(pids, key=lambda pid: (score.get(pid, 0.0), -paragraph_rows[pid]["idx"]))
            specs.append(
                _HitSpec(
                    ref=ref,
                    best_paragraph_id=best,
                    matches=len(pids),
                    score=score.get(best, 0.0),
                    is_lexical=best in lexical_set,
                    lexical_matches=sum(1 for pid in pids if pid in lexical_set),
                    paragraphs=len(pids),
                )
            )
        return _QueryEvidence(specs=specs, paragraph_rows=paragraph_rows)

    def _paragraphs_by_id(self, ids: set[int]) -> dict[int, sqlite3.Row]:
        found: dict[int, sqlite3.Row] = {}
        pending = sorted(ids)
        for start in range(0, len(pending), SQL_BATCH):
            batch = pending[start : start + SQL_BATCH]
            placeholders = ",".join("?" * len(batch))
            for row in self._conn.execute(f"SELECT * FROM paragraphs WHERE id IN ({placeholders})", batch):
                found[row["id"]] = row
        return found

    def _paragraph_counts(self) -> dict[str, int]:
        return {
            row["ref"]: row["n"]
            for row in self._conn.execute("SELECT ref, COUNT(*) AS n FROM paragraphs GROUP BY ref")
        }

    def _snippet(self, query: str, paragraph_id: int, is_lexical: bool, text: str) -> str:
        if is_lexical:
            expression = _fts_expression(query)
            row = self._conn.execute(
                "SELECT snippet(paragraph_fts, 0, '', '', '...', 32) AS s "
                "FROM paragraph_fts WHERE rowid = ? AND paragraph_fts MATCH ?",
                (paragraph_id, expression),
            ).fetchone()
            if row is not None:
                return row["s"]
        return text[:200] + ("..." if len(text) > 200 else "")

    def _citation_ref(self, record_row: sqlite3.Row, paragraph_row: sqlite3.Row | None) -> str:
        if record_row["kind"] == "source" and paragraph_row is not None and paragraph_row["anchor"] is not None:
            return refs.render(refs.SourceRef(record_row["ref"], anchor=paragraph_row["anchor"]))
        return record_row["ref"]

    def _record_date(self, row: sqlite3.Row) -> Date:
        return Date(iso=row["iso"], confidence=row["confidence"], granularity=row["granularity"], text=row["date_text"])

    # -- search -----------------------------------------------------------------

    def search(
        self,
        query: str = "",
        from_: str | None = None,
        to: str | None = None,
        who: str | None = None,
        kind: str | None = None,
        cursor: str | None = None,
    ) -> SearchReply:
        """design.md "Two tools": every filter optional; an empty query with
        a date range is a timeline browse. A cursor alone resumes its
        server-issued scope; any other argument alongside it is rejected.
        An assignment cursor (a ``chunks`` row's) executes exactly its own
        slice of the plan, in plan order, and pages like any other."""
        scope = segment = None
        if cursor is not None:
            if query or from_ or to or who or kind:
                raise CursorError("a cursor resumes its own scope alone; no other argument may accompany it")
            state = _decode_cursor(cursor)
            if state.get("t") != "search":
                raise CursorError(f"not a search cursor: {cursor!r}")
            if state["rev"] != self.index_revision:
                raise CursorError(
                    "index revision changed since this cursor was issued; restart the original scope "
                    f"(query={state['query']!r}, from={state['from']!r}, to={state['to']!r}, "
                    f"who={state['who']!r}, kind={state['kind']!r})"
                )
            query, from_, to, who, kind = state["query"], state["from"], state["to"], state["who"], state["kind"]
            offsets = state["off"]
            scope = state.get("scope")
            segment = tuple(state["seg"]) if state.get("seg") else None
        else:
            offsets = {"hits": 0, "month": 0, "covered": 0, "chunks": 0}

        who_matches = self._alias_matching_refs(self._alias_set(who)) if who else None
        eligible = self._eligible_refs(from_=from_, to=to, who_matches=who_matches, kind=kind)
        record_rows = {row["ref"]: row for row in self._all_record_rows() if row["ref"] in eligible}

        if _fts_expression(query):
            evidence = self._query_evidence(
                query, eligible, from_=from_, to=to, kind=kind, who_matches=who_matches
            )
            hit_specs = sorted(evidence.specs, key=lambda spec: (-spec.score, spec.ref))
            paragraph_rows = evidence.paragraph_rows
            hit_cap = QUERY_HIT_CAP
        else:
            counts = self._paragraph_counts()
            hit_specs = [
                _HitSpec(ref=ref, best_paragraph_id=None, matches=1, score=0.0, is_lexical=False, paragraphs=counts.get(ref, 0))
                for ref in sorted(record_rows, key=lambda ref: _browse_key(record_rows[ref]))
            ]
            paragraph_rows = {}
            hit_cap = BROWSE_HIT_CAP

        covered_all = self._covered(from_, to)
        cursor_base = {
            "t": "search",
            "rev": self.index_revision,
            "query": query,
            "from": from_,
            "to": to,
            "who": who,
            "kind": kind,
        }
        if scope is None:
            plan = self._chunk_plan([spec.ref for spec in hit_specs], record_rows, covered_all)
        else:
            # The assignment's records: those whose plan key lies within the
            # cursor's scope, in plan order. The plan is recomputed at the
            # same revision, so the slice is the one that was issued.
            lo, hi = scope
            spec_of = {spec.ref: spec for spec in hit_specs}
            candidates = self._plan_candidates(list(spec_of), record_rows, covered_all)
            scoped = [ref for ref in candidates if lo <= _plan_key(record_rows[ref]) <= hi]
            hit_specs = [spec_of[ref] for ref in scoped]
            plan = [self._scoped_assignment(scoped, record_rows, segment)] if scoped else []
            cursor_base["scope"] = scope
            if segment:
                cursor_base["seg"] = list(segment)

        evidence_refs = [spec.ref for spec in hit_specs]
        lexical_records = sum(1 for spec in hit_specs if spec.lexical_matches)
        lexical_paragraphs = sum(spec.lexical_matches for spec in hit_specs)
        evidence_paragraphs = sum(spec.paragraphs for spec in hit_specs)
        tokens = sum(record_rows[ref]["tokens"] for ref in evidence_refs)
        undated = sum(1 for ref in evidence_refs if record_rows[ref]["confidence"] == "unknown")
        inferred = sum(1 for ref in evidence_refs if record_rows[ref]["confidence"] == "inferred")
        by_month_all = self._by_month(evidence_refs, record_rows)
        chunks_all = [self._chunk_row(assignment, record_rows, cursor_base) for assignment in plan]

        LIST_PAGE_CAP = 500
        hit_specs_page = list(hit_specs[offsets["hits"] : offsets["hits"] + hit_cap])
        month_page = list(by_month_all[offsets["month"] :][:LIST_PAGE_CAP])
        covered_page = list(covered_all[offsets["covered"] :][:LIST_PAGE_CAP])
        chunks_page = list(chunks_all[offsets["chunks"] :][:LIST_PAGE_CAP])

        def build(hit_specs_page, month_page, covered_page, chunks_page) -> SearchReply:
            hits = [
                self._build_hit(spec, record_rows[spec.ref], paragraph_rows.get(spec.best_paragraph_id), query)
                for spec in hit_specs_page
            ]
            return SearchReply(
                indexing=self.indexing_state,
                index_revision=self.index_revision,
                corpus_revision=self._ledger.corpus_revision(),
                lexical_records=lexical_records,
                lexical_paragraphs=lexical_paragraphs,
                evidence_records=len(evidence_refs),
                evidence_paragraphs=evidence_paragraphs,
                tokens=tokens,
                by_month=month_page,
                undated=undated,
                inferred=inferred,
                covered=covered_page,
                chunks=chunks_page,
                hits=hits,
                shown=len(hits),
                total_shown_of=len(evidence_refs),
                continuation=None,
            )

        def fits(reply: SearchReply) -> bool:
            next_off = {"hits": offsets["hits"] + len(reply.hits),
                        "month": offsets["month"] + len(reply.by_month),
                        "covered": offsets["covered"] + len(reply.covered),
                        "chunks": offsets["chunks"] + len(reply.chunks)}
            more = (next_off["hits"] < len(hit_specs) or next_off["month"] < len(by_month_all)
                    or next_off["covered"] < len(covered_all) or next_off["chunks"] < len(chunks_all))
            continuation = _encode_cursor({**cursor_base, "off": next_off}) if more else None
            measured = _with_reply_tokens(replace(reply, continuation=continuation))
            return estimate_tokens(measured.text()) <= self.reply_token_budget

        candidate = build(hit_specs_page, month_page, covered_page, chunks_page)
        # The normal page (already capped at 100/500 per list) usually fits
        # in one render; only an oversized item (many long rows) needs
        # trimming - shrink whichever list is currently largest until it
        # fits, so every list still advances rather than a ref becoming
        # unreachable (acceptance #22 test 3). A hit's title is capped for
        # display (Hit.line), so a hit can no longer be the oversized item;
        # nothing is ever sent over budget, so the loop pops all the way to
        # nothing rather than force a failing candidate through.
        while not fits(candidate):
            pages = {"month": month_page, "covered": covered_page, "chunks": chunks_page, "hits": hit_specs_page}
            if not any(pages.values()):
                break
            name = max(pages, key=lambda key: len(pages[key]))
            pages[name].pop()
            candidate = build(hit_specs_page, month_page, covered_page, chunks_page)

        next_offsets = {
            "hits": offsets["hits"] + len(hit_specs_page),
            "month": offsets["month"] + len(month_page),
            "covered": offsets["covered"] + len(covered_page),
            "chunks": offsets["chunks"] + len(chunks_page),
        }
        more = (
            next_offsets["hits"] < len(hit_specs)
            or next_offsets["month"] < len(by_month_all)
            or next_offsets["covered"] < len(covered_all)
            or next_offsets["chunks"] < len(chunks_all)
        )
        continuation = None
        if more:
            continuation = _encode_cursor({**cursor_base, "off": next_offsets})
            if next_offsets == offsets:
                raise ValueError("search scope or metadata cannot fit the reply budget; narrow the scope")
        candidate = replace(candidate, continuation=continuation)
        return _with_reply_tokens(candidate)

    def _build_hit(self, spec: "_HitSpec", record_row: sqlite3.Row, paragraph_row: sqlite3.Row | None, query: str) -> Hit:
        date = self._record_date(record_row)
        ref = self._citation_ref(record_row, paragraph_row)
        snippet = ""
        if query and paragraph_row is not None:
            snippet = self._snippet(query, paragraph_row["id"], spec.is_lexical, paragraph_row["text"])
        return Hit(ref=ref, kind=record_row["kind"], date=date, title=record_row["title"], matches=spec.matches, snippet=snippet)

    def _by_month(self, refs_in_scope: list[str], record_rows: dict[str, sqlite3.Row]) -> list[MonthRow]:
        buckets: dict[str, list[int]] = {}
        for ref in refs_in_scope:
            row = record_rows[ref]
            if row["confidence"] == "unknown":
                continue
            buckets.setdefault(_month_key(row["iso"]), []).append(row["tokens"])
        return [MonthRow(month=month, records=len(toks), tokens=sum(toks)) for month, toks in sorted(buckets.items())]

    def _covered(self, from_: str | None, to: str | None) -> list[CoveredRow]:
        if from_ is None and to is None:
            return []
        rows = []
        current_corpus_revision = self._ledger.corpus_revision()
        for row in self._conn.execute(
            "SELECT ref, window_from, window_to, corpus_revision, coverage_complete FROM records "
            "WHERE kind = 'note' AND type = 'digest' AND window_from IS NOT NULL"
        ):
            if not row["coverage_complete"]:
                continue
            if row["corpus_revision"] != current_corpus_revision:
                continue
            window = (row["window_from"], row["window_to"])
            if windows_overlap(window, from_, to):
                rows.append(CoveredRow(ref=row["ref"], window=window))
        rows.sort(key=lambda r: (r.window[0], r.ref))
        return rows

    # -- chunks -----------------------------------------------------------------

    def _plan_candidates(
        self, evidence_refs: list[str], record_rows: dict[str, sqlite3.Row], covered_rows: list[CoveredRow]
    ) -> list[str]:
        """The evidence records a plan assigns, in plan order: everything
        except a source record proven inside an eligible digest window.
        Membership is conservative (CONTEXT.md "Coverage"): only an
        ``exact`` (day-granularity) source record is ever excluded; a
        coarse or unknown date, or any note or manuscript, is never
        suppressed by overlap alone."""
        windows = [row.window for row in covered_rows]

        def is_covered(row: sqlite3.Row) -> bool:
            if row["kind"] != "source" or row["confidence"] != "exact":
                return False
            return any(start <= row["iso"] <= end for start, end in windows)

        candidates = [ref for ref in evidence_refs if not is_covered(record_rows[ref])]
        candidates.sort(key=lambda ref: _plan_key(record_rows[ref]))
        return candidates

    def _chunk_plan(
        self, evidence_refs: list[str], record_rows: dict[str, sqlite3.Row], covered_rows: list[CoveredRow]
    ) -> list[_Assignment]:
        """#10 ss4: partition once in plan order, greedily filled by
        calendar-day bucket under ``chunk_tokens``; a bucket over budget
        splits by ref order; a single record over budget becomes one
        assignment per bounded read segment, each marked ``k of n``."""
        candidates = self._plan_candidates(evidence_refs, record_rows, covered_rows)
        if not candidates:
            return []

        buckets: list[tuple[str, list[str]]] = []
        for ref in candidates:
            row = record_rows[ref]
            key = "unknown" if row["confidence"] == "unknown" else row["iso"]
            if buckets and buckets[-1][0] == key:
                buckets[-1][1].append(ref)
            else:
                buckets.append((key, [ref]))

        assignments: list[_Assignment] = []
        current_refs: list[str] = []
        current_tokens = 0

        def flush() -> None:
            nonlocal current_refs, current_tokens
            if current_refs:
                assignments.append(_Assignment(refs=list(current_refs), tokens=current_tokens))
            current_refs = []
            current_tokens = 0

        for _key, refs_in_bucket in buckets:
            bucket_tokens = sum(record_rows[ref]["tokens"] for ref in refs_in_bucket)
            if current_refs and current_tokens + bucket_tokens > self.chunk_tokens:
                flush()
            if bucket_tokens <= self.chunk_tokens:
                current_refs.extend(refs_in_bucket)
                current_tokens += bucket_tokens
                continue
            # This bucket alone exceeds budget: split by ref order.
            for ref in refs_in_bucket:
                ref_tokens = record_rows[ref]["tokens"]
                if ref_tokens > self.chunk_tokens:
                    flush()
                    segments = math.ceil(ref_tokens / self.chunk_tokens)
                    for k in range(1, segments + 1):
                        assignments.append(self._scoped_assignment([ref], record_rows, (k, segments)))
                    continue
                if current_refs and current_tokens + ref_tokens > self.chunk_tokens:
                    flush()
                current_refs.append(ref)
                current_tokens += ref_tokens
        flush()
        return assignments

    def _scoped_assignment(
        self, refs_in_scope: list[str], record_rows: dict[str, sqlite3.Row], segment: tuple[int, int] | None
    ) -> _Assignment:
        tokens = sum(record_rows[ref]["tokens"] for ref in refs_in_scope)
        if segment is not None:
            k, _n = segment
            tokens = max(0, min(self.chunk_tokens, tokens - (k - 1) * self.chunk_tokens))
        return _Assignment(refs=refs_in_scope, tokens=tokens, segment=segment)

    def _chunk_row(self, assignment: _Assignment, record_rows: dict[str, sqlite3.Row], cursor_base: dict) -> ChunkRow:
        first_row, last_row = record_rows[assignment.refs[0]], record_rows[assignment.refs[-1]]
        frm = "unknown" if first_row["confidence"] == "unknown" else first_row["iso"]
        to_ = "unknown" if last_row["confidence"] == "unknown" else last_row["iso"]
        payload = {
            **cursor_base,
            "off": {"hits": 0, "month": 0, "covered": 0, "chunks": 0},
            "scope": [_plan_key(first_row), _plan_key(last_row)],
        }
        if assignment.segment is not None:
            payload["seg"] = list(assignment.segment)
        else:
            payload.pop("seg", None)
        return ChunkRow(
            from_=frm,
            to=to_,
            records=len(assignment.refs),
            tokens=assignment.tokens,
            cursor=_encode_cursor(payload),
            segment=assignment.segment,
        )


    # -- read -------------------------------------------------------------------

    def read(self, ref: str = "", cursor: str | None = None) -> ReadReply:
        """Read exact payloads with independently paged metadata and bounded cursors."""
        start_para = start_char = metadata_offset = 0
        if cursor is not None:
            state = _decode_cursor(cursor)
            if state.get("t") != "read":
                raise CursorError("not a read cursor")
            selected = state.get("ref") or self._get_meta("read_ref:" + state.get("ref_id", ""))
            if not selected:
                raise CursorError("read selection expired; restart the original read")
            if ref and ref != selected:
                raise CursorError("a cursor resumes its own selection alone; ref must match or be omitted")
            if state["rev"] != self.index_revision:
                raise CursorError("index revision changed since this cursor was issued; restart the original read")
            ref = selected
            start_para, start_char = state["off"]["para"], state["off"]["char"]
            metadata_offset = state.get("meta", 0)

        parsed = refs.parse(ref)
        labels, units = self._read_selection(parsed)
        header = "\n".join(labels) + "\n\n" if labels else ""
        large_metadata = len(header.encode("utf-8")) > 4000
        selection = {"ref": ref}
        if len(ref.encode("utf-8")) > 512:
            key = hashlib.sha256(ref.encode("utf-8")).hexdigest()
            self._set_meta("read_ref:" + key, ref)
            self._conn.commit()
            selection = {"ref_id": key}

        def continuation(off, meta):
            return _encode_cursor({"t": "read", "rev": self.index_revision,
                                   **selection, "off": off, "meta": meta})

        # Reserve transport space, then check the exact rendered reply below.
        budget = self.reply_token_budget * 4 - 1000
        while budget > 0:
            if large_metadata and metadata_offset < len(header):
                remaining = header[metadata_offset:]
                cut = _safe_char_cut(remaining, budget)
                if not cut:
                    cut = len(remaining.encode("utf-8")[:budget].decode("utf-8", errors="ignore"))
                end = metadata_offset + cut
                next_cursor = continuation({"para": start_para, "char": start_char}, end)
                reply = ReadReply(ref, (), (), next_cursor, metadata=header[metadata_offset:end])
            else:
                shown_labels = () if large_metadata else tuple(labels)
                label_bytes = 0 if large_metadata else len(header.encode("utf-8"))
                pieces, next_off = _paginate(units, start_para, start_char, budget - label_bytes)
                next_cursor = continuation(next_off, metadata_offset) if next_off else None
                reply = ReadReply(ref, shown_labels, tuple(pieces), next_cursor,
                                  metadata="" if large_metadata else None)
            reply = _with_read_reply_tokens(reply)
            if estimate_tokens(reply.text()) <= self.reply_token_budget:
                return reply
            budget -= max(256, (estimate_tokens(reply.text()) - self.reply_token_budget) * 4)
        raise ValueError("read cannot fit its transport metadata")

    def _read_selection(self, parsed: "refs.Ref") -> tuple[list[str], list[tuple[str | None, str]]]:
        """The selection's transport labels (the ref line, then markers) and
        the ``(label, paragraph)`` units ``_paginate`` walks. Source
        paragraphs are labelled with their citation so a reader can cite
        ``p17`` from what it read; notes and manuscript are positional and
        carry no per-paragraph label."""
        if isinstance(parsed, refs.SourceRef):
            return self._read_source(parsed)
        if isinstance(parsed, refs.ManuscriptRef):
            return self._read_manuscript_section(parsed)
        return self._read_path(parsed)

    def _read_source(self, parsed: "refs.SourceRef") -> tuple[list[str], list[tuple[str | None, str]]]:
        canonical = refs.render(parsed)
        record_row = self._conn.execute("SELECT * FROM records WHERE ref = ?", (parsed.id,)).fetchone()
        labels = [self._read_header_line(canonical, record_row)] if record_row is not None else []

        def citation(anchor: int) -> str:
            return refs.render(refs.SourceRef(parsed.id, anchor=anchor))

        if parsed.anchor is None:
            if record_row is None:
                raise BadRef(f"no such record: {parsed.id}")
            rows = self._conn.execute(
                "SELECT anchor, text FROM paragraphs WHERE ref = ? ORDER BY idx", (parsed.id,)
            ).fetchall()
            return labels, [(citation(row["anchor"]), row["text"]) for row in rows]
        if parsed.end is not None or parsed.tail:
            result = self._ledger.range(canonical)
            if isinstance(result, str):
                return labels + [result], []
            return labels, [(citation(p), text) for p, text in result]
        # A single paragraph anchor, live or retired, straight from the ledger.
        if parsed.anchor in self._ledger.live_anchors(parsed.id):
            return labels, [(canonical, self._ledger.text(canonical))]
        # Retired: the ledger's structured accessor gives the exact retired
        # text apart from the marker and pointer labels (ADR-0001), so
        # neither is ever split out of a composed string.
        retired = self._ledger.retired_anchor(canonical)
        return labels + [retired.marker(canonical), retired.pointer()], [(canonical, retired.text)]

    def _read_header_line(self, canonical: str, record_row: sqlite3.Row) -> str:
        date = self._record_date(record_row)
        return f"{canonical}  {display_date(date)}  {record_row['kind']}  {record_row['title']}"

    def _paragraph_texts(self, ref: str) -> list[str]:
        rows = self._conn.execute("SELECT text FROM paragraphs WHERE ref = ? ORDER BY idx", (ref,)).fetchall()
        return [row["text"] for row in rows]

    def _read_path(self, parsed: "refs.PathRef") -> tuple[list[str], list[tuple[str | None, str]]]:
        record_row = self._conn.execute("SELECT * FROM records WHERE ref = ?", (parsed.path,)).fetchone()
        if record_row is None:
            raise BadRef(f"no such record: {parsed.path}")
        labels = [self._read_header_line(parsed.path, record_row)]
        if record_row["kind"] == "note":
            labels += [f"warning: {warning}" for warning in json.loads(record_row["warnings_json"])]
        return labels, [(None, text) for text in self._paragraph_texts(parsed.path)]

    def _read_manuscript_section(self, parsed: "refs.ManuscriptRef") -> tuple[list[str], list[tuple[str | None, str]]]:
        record_row = self._conn.execute("SELECT * FROM records WHERE ref = ?", (parsed.path,)).fetchone()
        if record_row is None:
            raise BadRef(f"no such record: {parsed.path}")
        paragraphs = tuple(self._paragraph_texts(parsed.path))
        section = _manuscript_section(paragraphs, parsed.heading, parsed.occurrence)
        labels = [self._read_header_line(refs.render(parsed), record_row)]
        return labels, [(None, text) for text in section]

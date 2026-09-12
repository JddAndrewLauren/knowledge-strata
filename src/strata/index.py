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
- ``read(ref, cursor)`` returns verbatim text for any ref shape, paged at
  paragraph or (inside an oversized paragraph) Unicode character boundaries.

Enumeration is simplified relative to a web-scale design: at this project's
target size (a personal corpus, python-stack.md), recomputing a query's full
sorted evidence list on every call and slicing it in Python is fast and,
importantly, easy to keep exactly deduplicated and stably ordered - the
property the acceptance gate actually requires. A single opaque cursor per
reply resumes every unfinished list (hits, by_month, covered, chunks)
together, rather than issuing one cursor per list; CONTEXT.md's "a cursor
alone resumes exact scope" is read as one cursor per reply, not per field.
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
from strata.record import Date, Record

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


@dataclass(frozen=True)
class _QueryEvidence:
    specs: list[_HitSpec]
    lexical_records: int
    lexical_paragraphs: int
    evidence_paragraphs: int
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
    while 0 < cut < len(text) and unicodedata.combining(text[cut]) != 0:
        cut -= 1
    return cut


def _paginate(segments: list[str], start_para: int, start_char: int, budget_bytes: int) -> tuple[str, dict | None]:
    """Walk ``segments`` (paragraph-sized units) from ``(start_para,
    start_char)``, filling ``budget_bytes``: whole segments join with a
    blank line; a segment that alone exceeds the remaining budget is cut at
    a safe character boundary and resumed exactly where it left off next
    time - no separator at the cut, so concatenating pages reproduces the
    exact text (design.md "Read")."""
    pieces: list[str] = []
    total_bytes = 0
    para, char = start_para, start_char
    while para < len(segments):
        remaining_text = segments[para][char:]
        remaining_bytes = len(remaining_text.encode("utf-8"))
        if total_bytes + remaining_bytes <= budget_bytes:
            pieces.append(remaining_text)
            total_bytes += remaining_bytes
            para += 1
            char = 0
            continue
        budget_left = budget_bytes - total_bytes
        cut = _safe_char_cut(remaining_text, budget_left) if budget_left > 0 else 0
        if cut <= 0:
            break
        pieces.append(remaining_text[:cut])
        return "\n\n".join(pieces), {"para": para, "char": char + cut}
    if para >= len(segments):
        return "\n\n".join(pieces), None
    return "\n\n".join(pieces), {"para": para, "char": char}


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
        head = f"{self.ref}  {display_date(self.date)}  {self.kind}  {self.title}"
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
    from_: str
    to: str
    records: int
    tokens: int
    cursor: str
    segments: int = 1

    def line(self) -> str:
        return f"{self.from_}..{self.to}  {self.records} records  ~{self.tokens}  {self.cursor}"


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
    ref: str
    body: str
    continuation: str | None
    reply_tokens: int = 0

    def text(self) -> str:
        body = self.body
        if self.continuation:
            body = f"{body}\n[continues: {self.continuation}]"
        return f"{body}\nreply_tokens ~{self.reply_tokens}"


def _with_read_reply_tokens(reply: ReadReply) -> ReadReply:
    draft = replace(reply, reply_tokens=0)
    return replace(draft, reply_tokens=estimate_tokens(draft.text()))


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
"""


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
    ):
        path = Path(path)
        is_new = not path.exists()
        self._ledger = ledger
        self._embedder = embedder
        self.chunk_tokens = chunk_tokens
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        # `semantic=False` degrades to lexical-only, same as no vec0 build
        # (python-stack.md ss2's VectorExtensionUnavailable path) - useful to
        # a caller and to tests isolating lexical behaviour from the
        # always-on top-200 semantic branch.
        self._vec_ok = self._load_vec() if semantic else False
        if is_new:
            self._set_meta("epoch", os.urandom(8).hex())
            self._set_meta("revision", "0")
        self._ensure_embedder_identity()
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- vec0 setup ---------------------------------------------------------

    def _load_vec(self) -> bool:
        try:
            import sqlite_vec
        except ImportError:
            return False
        try:
            self._conn.enable_load_extension(True)
            sqlite_vec.load(self._conn)
            self._conn.enable_load_extension(False)
        except (AttributeError, sqlite3.NotSupportedError, sqlite3.OperationalError):
            return False
        return True

    def _vec_table_ready(self) -> bool:
        if not self._vec_ok:
            return False
        row = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE name = 'paragraph_vec'"
        ).fetchone()
        return row is not None

    def _ensure_embedder_identity(self) -> None:
        stored_model = self._get_meta("embedder_model")
        stored_dim = self._get_meta("embedder_dim")
        current_model, current_dim = self._embedder.model_id, str(self._embedder.dim)
        if stored_model == current_model and stored_dim == current_dim:
            return
        # A model change invalidates every cached vector; re-embed whatever
        # is already indexed right away, rather than waiting for the next
        # sync() to notice a "changed" record it will not see as changed
        # (design.md ss "Search": an embedder-identity row forces this).
        self._conn.execute("DROP TABLE IF EXISTS paragraph_vec")
        self._conn.execute("DELETE FROM embedding_cache")
        if self._vec_ok:
            self._conn.execute(
                f"CREATE VIRTUAL TABLE paragraph_vec USING vec0("
                f"paragraph_id INTEGER PRIMARY KEY, embedding FLOAT[{self._embedder.dim}])"
            )
        self._set_meta("embedder_model", current_model)
        self._set_meta("embedder_dim", current_dim)
        existing = self._conn.execute("SELECT id, text, content_hash FROM paragraphs").fetchall()
        if existing:
            new_vectors = [(row["id"], row["content_hash"]) for row in existing]
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

    def mark_complete(self, complete: bool = True) -> None:
        self._set_meta("indexing", "complete" if complete else "incomplete")
        self._conn.commit()

    @property
    def indexing_state(self) -> str:
        return self._get_meta("indexing") or "incomplete"

    # -- sync -----------------------------------------------------------------

    def sync(self, records: list[Record], *, complete: bool = True) -> dict:
        """Diff by whole-record content hash: an unchanged record is
        untouched; a changed or new one is fully replaced; a vanished one is
        deleted. Paragraph embeddings are cached by exact text
        (CONTEXT.md: match key), so an unchanged paragraph never re-embeds
        even inside a changed record."""
        existing = {
            row["ref"]: row["content_hash"] for row in self._conn.execute("SELECT ref, content_hash FROM records")
        }
        incoming = {record.ref: record for record in records}
        changed_any = False

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
        new_vectors: list[tuple[int, str]] = []
        for idx, text in enumerate(record.paragraphs):
            p_hash = _hash_text(text)
            anchor = anchors[idx] if idx < len(anchors) else None
            cur = self._conn.execute(
                "INSERT INTO paragraphs (ref, idx, anchor, text, content_hash) VALUES (?,?,?,?,?)",
                (record.ref, idx, anchor, text, p_hash),
            )
            pid = cur.lastrowid
            self._conn.execute("INSERT INTO paragraph_fts (rowid, text) VALUES (?, ?)", (pid, text))
            new_vectors.append((pid, p_hash))
        self._embed_new(new_vectors, {text_hash: text for text_hash, text in zip((h for _, h in new_vectors), record.paragraphs)})

    def _embed_new(self, new_vectors: list[tuple[int, str]], text_of_hash: dict[str, str]) -> None:
        if not new_vectors or not self._vec_ok:
            return
        to_embed: list[str] = []
        to_embed_hashes: list[str] = []
        cached: dict[str, tuple[float, ...]] = {}
        for _, p_hash in new_vectors:
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
            for pid, p_hash in new_vectors:
                self._conn.execute(
                    "INSERT INTO paragraph_vec (paragraph_id, embedding) VALUES (?, ?)",
                    (pid, _pack_vector(cached[p_hash])),
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

    def _eligible_refs(self, *, from_: str | None, to: str | None, who: str | None, kind: str | None) -> set[str]:
        who_matches = self._alias_matching_refs(self._alias_set(who)) if who else None
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
        phrase = _fts_phrase(query)
        return self._conn.execute(
            "SELECT p.id AS id, p.ref AS ref, p.idx AS idx FROM paragraph_fts "
            "JOIN paragraphs p ON p.id = paragraph_fts.rowid "
            "WHERE paragraph_fts MATCH ? ORDER BY bm25(paragraph_fts), p.ref, p.idx",
            (phrase,),
        ).fetchall()

    def _semantic_paragraph_ids(self, query: str, candidate_ids: list[int]) -> list[int]:
        if not candidate_ids or not self._vec_table_ready():
            return []
        placeholders = ",".join("?" * len(candidate_ids))
        qvector = _pack_vector(self._embedder.embed_query(query))
        k = min(SEMANTIC_K, len(candidate_ids))
        rows = self._conn.execute(
            f"SELECT paragraph_id FROM paragraph_vec WHERE embedding MATCH ? AND k = ? "
            f"AND paragraph_id IN ({placeholders}) ORDER BY distance",
            (qvector, k, *candidate_ids),
        ).fetchall()
        return [row["paragraph_id"] for row in rows]

    def _query_evidence(self, query: str, eligible: set[str]) -> "_QueryEvidence":
        all_lexical = [row for row in self._lexical_paragraph_rows(query) if row["ref"] in eligible]
        lexical_ids = [row["id"] for row in all_lexical]
        candidate_ids = [
            row["id"] for row in self._conn.execute("SELECT id FROM paragraphs WHERE ref IN ({})".format(
                ",".join("?" * len(eligible)) or "NULL"
            ), tuple(eligible))
        ] if eligible else []
        semantic_ids = self._semantic_paragraph_ids(query, candidate_ids)

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
                )
            )
        lexical_refs = {paragraph_rows[pid]["ref"] for pid in lexical_ids}
        return _QueryEvidence(
            specs=specs,
            lexical_records=len(lexical_refs),
            lexical_paragraphs=len(lexical_ids),
            evidence_paragraphs=len(evidence_ids),
            paragraph_rows=paragraph_rows,
        )

    def _paragraphs_by_id(self, ids: set[int]) -> dict[int, sqlite3.Row]:
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        rows = self._conn.execute(f"SELECT * FROM paragraphs WHERE id IN ({placeholders})", tuple(ids)).fetchall()
        return {row["id"]: row for row in rows}

    def _snippet(self, query: str, paragraph_id: int, is_lexical: bool, text: str) -> str:
        if is_lexical:
            phrase = _fts_phrase(query)
            row = self._conn.execute(
                "SELECT snippet(paragraph_fts, 0, '', '', '...', 32) AS s "
                "FROM paragraph_fts WHERE rowid = ? AND paragraph_fts MATCH ?",
                (paragraph_id, phrase),
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
        server-issued scope; any other argument alongside it is rejected."""
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
        else:
            offsets = {"hits": 0, "month": 0, "covered": 0, "chunks": 0}

        eligible = self._eligible_refs(from_=from_, to=to, who=who, kind=kind)
        record_rows = {row["ref"]: row for row in self._all_record_rows() if row["ref"] in eligible}

        if query:
            evidence = self._query_evidence(query, eligible)
            hit_specs = sorted(evidence.specs, key=lambda spec: (-spec.score, spec.ref))
            lexical_records, lexical_paragraphs = evidence.lexical_records, evidence.lexical_paragraphs
            evidence_paragraphs = evidence.evidence_paragraphs
            paragraph_rows = evidence.paragraph_rows
            hit_cap = QUERY_HIT_CAP
        else:
            hit_specs = [
                _HitSpec(ref=ref, best_paragraph_id=None, matches=1, score=0.0, is_lexical=False)
                for ref in sorted(record_rows, key=lambda ref: _browse_key(record_rows[ref]))
            ]
            lexical_records = lexical_paragraphs = 0
            evidence_paragraphs = sum(
                self._conn.execute("SELECT COUNT(*) AS n FROM paragraphs WHERE ref = ?", (ref,)).fetchone()["n"]
                for ref in record_rows
            )
            paragraph_rows = {}
            hit_cap = BROWSE_HIT_CAP

        evidence_refs = [spec.ref for spec in hit_specs]
        tokens = sum(record_rows[ref]["tokens"] for ref in evidence_refs)
        undated = sum(1 for ref in evidence_refs if record_rows[ref]["confidence"] == "unknown")
        inferred = sum(1 for ref in evidence_refs if record_rows[ref]["confidence"] == "inferred")
        by_month_all = self._by_month(evidence_refs, record_rows)
        covered_all = self._covered(from_, to)
        chunks_all = self._chunk_plan(evidence_refs, record_rows, covered_all, query=query, from_=from_, to=to, who=who, kind=kind)

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
            return estimate_tokens(_render_reply(reply)) <= REPLY_TOKEN_BUDGET

        candidate = build(hit_specs_page, month_page, covered_page, chunks_page)
        # The normal page (already capped at 100/500 per list) usually fits
        # in one render; only an oversized item (a very long title, or many
        # long rows) needs trimming - shrink whichever list is currently
        # largest until it fits, so every list still advances rather than
        # a ref becoming unreachable (acceptance #22 test 3). If only one
        # item remains and it still does not fit, it is forced through
        # rather than silently dropped.
        while not fits(candidate):
            pages = {"month": month_page, "covered": covered_page, "chunks": chunks_page, "hits": hit_specs_page}
            if sum(len(page) for page in pages.values()) <= 1:
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
            continuation = _encode_cursor(
                {
                    "t": "search",
                    "rev": self.index_revision,
                    "query": query,
                    "from": from_,
                    "to": to,
                    "who": who,
                    "kind": kind,
                    "off": next_offsets,
                }
            )
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

    def _chunk_plan(
        self,
        evidence_refs: list[str],
        record_rows: dict[str, sqlite3.Row],
        covered_rows: list[CoveredRow],
        *,
        query: str,
        from_: str | None,
        to: str | None,
        who: str | None,
        kind: str | None,
    ) -> list[ChunkRow]:
        """#10 ss4: partition once in date/ref order (unknown last, partial
        dates by period start), greedily filled by calendar-day bucket under
        ``chunk_tokens``; a bucket over budget splits by ref order; a single
        record over budget gets its own assignment, marked with how many
        bounded read segments it will take. Coverage is conservative: only
        an ``exact`` (day-granularity) record fully inside an eligible
        digest window is excluded (CONTEXT.md "Coverage")."""
        windows = [row.window for row in covered_rows]

        def is_covered(row: sqlite3.Row) -> bool:
            if row["confidence"] != "exact":
                return False
            return any(row["iso"] >= start and row["iso"] <= end for start, end in windows)

        candidates = [ref for ref in evidence_refs if not is_covered(record_rows[ref])]
        if not candidates:
            return []

        def sort_key(ref: str) -> tuple:
            row = record_rows[ref]
            return (row["confidence"] == "unknown", row["iso"], ref)

        candidates.sort(key=sort_key)

        buckets: list[tuple[str, list[str]]] = []
        for ref in candidates:
            row = record_rows[ref]
            key = "unknown" if row["confidence"] == "unknown" else row["iso"]
            if buckets and buckets[-1][0] == key:
                buckets[-1][1].append(ref)
            else:
                buckets.append((key, [ref]))

        assignments: list[ChunkRow] = []
        current_refs: list[str] = []
        current_tokens = 0

        def flush() -> None:
            nonlocal current_refs, current_tokens
            if not current_refs:
                return
            first_row, last_row = record_rows[current_refs[0]], record_rows[current_refs[-1]]
            frm = "unknown" if first_row["confidence"] == "unknown" else first_row["iso"]
            to_ = "unknown" if last_row["confidence"] == "unknown" else last_row["iso"]
            cursor = _encode_cursor(
                {
                    "t": "search",
                    "rev": self.index_revision,
                    "query": query,
                    "from": from_,
                    "to": to,
                    "who": who,
                    "kind": kind,
                    "off": {"hits": 0, "month": 0, "covered": 0, "chunks": 0},
                    "scope": list(current_refs),
                }
            )
            segments = 1
            if len(current_refs) == 1 and record_rows[current_refs[0]]["tokens"] > self.chunk_tokens:
                segments = math.ceil(record_rows[current_refs[0]]["tokens"] / self.chunk_tokens)
            assignments.append(
                ChunkRow(from_=frm, to=to_, records=len(current_refs), tokens=current_tokens, cursor=cursor, segments=segments)
            )
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
                if current_refs and current_tokens + ref_tokens > self.chunk_tokens:
                    flush()
                current_refs.append(ref)
                current_tokens += ref_tokens
                if ref_tokens > self.chunk_tokens:
                    flush()  # this one record alone is oversized
        flush()
        return assignments

    # -- read -------------------------------------------------------------------

    def read(self, ref: str = "", cursor: str | None = None) -> ReadReply:
        """Verbatim text for any ref shape (design.md "Read"). A cursor
        alone resumes its own selection and offset; ``ref`` may be omitted
        or must match it."""
        if cursor is not None:
            state = _decode_cursor(cursor)
            if state.get("t") != "read":
                raise CursorError(f"not a read cursor: {cursor!r}")
            if ref and ref != state["ref"]:
                raise CursorError("a cursor resumes its own selection alone; ref must match or be omitted")
            if state["rev"] != self.index_revision:
                raise CursorError(
                    f"index revision changed since this cursor was issued; restart read({state['ref']!r})"
                )
            ref = state["ref"]
            start_para, start_char = state["off"]["para"], state["off"]["char"]
        else:
            start_para = start_char = 0

        parsed = refs.parse(ref)
        segments = self._read_segments(parsed)

        budget_bytes = REPLY_TOKEN_BUDGET * 4 - 400  # headroom for the continuation/reply_tokens lines
        body, next_off = _paginate(segments, start_para, start_char, budget_bytes)
        continuation = None
        if next_off is not None:
            continuation = _encode_cursor({"t": "read", "rev": self.index_revision, "ref": ref, "off": next_off})
        reply = ReadReply(ref=ref, body=body, continuation=continuation)
        return _with_read_reply_tokens(reply)

    def _read_segments(self, parsed: "refs.Ref") -> list[str]:
        """The ordered list of text units ``_paginate`` walks: one leading
        heading/metadata segment, then the payload paragraphs."""
        if isinstance(parsed, refs.SourceRef):
            return self._read_source_segments(parsed)
        if isinstance(parsed, refs.ManuscriptRef):
            return self._read_manuscript_section_segments(parsed)
        return self._read_path_segments(parsed)

    def _read_source_segments(self, parsed: "refs.SourceRef") -> list[str]:
        canonical = refs.render(parsed)
        record_row = self._conn.execute("SELECT * FROM records WHERE ref = ?", (parsed.id,)).fetchone()
        if parsed.anchor is None:
            if record_row is None:
                raise BadRef(f"no such record: {parsed.id}")
            paragraphs = self._paragraph_texts(parsed.id)
            header = self._read_header_line(record_row)
            return [header] + list(paragraphs)
        if parsed.end is not None or parsed.tail:
            result = self._ledger.range(canonical)
            if isinstance(result, str):
                return [result]
            texts = [text for _p, text in result]
            header = self._read_header_line(record_row) if record_row is not None else parsed.id
            return [header] + texts
        # a single paragraph anchor: live or retired, straight from the ledger.
        text = self._ledger.text(canonical)
        if parsed.anchor in self._ledger.live_anchors(parsed.id) and record_row is not None:
            header = self._read_header_line(record_row)
            return [header, text]
        return [text]

    def _read_header_line(self, record_row: sqlite3.Row) -> str:
        date = self._record_date(record_row)
        return f"{record_row['ref']}  {display_date(date)}  {record_row['kind']}  {record_row['title']}"

    def _paragraph_texts(self, ref: str) -> list[str]:
        rows = self._conn.execute("SELECT text FROM paragraphs WHERE ref = ? ORDER BY idx", (ref,)).fetchall()
        return [row["text"] for row in rows]

    def _read_path_segments(self, parsed: "refs.PathRef") -> list[str]:
        record_row = self._conn.execute("SELECT * FROM records WHERE ref = ?", (parsed.path,)).fetchone()
        if record_row is None:
            raise BadRef(f"no such record: {parsed.path}")
        header = self._read_header_line(record_row)
        segments = [header]
        if record_row["kind"] == "note":
            warnings = json.loads(record_row["warnings_json"])
            segments += [f"warning: {warning}" for warning in warnings]
        segments += self._paragraph_texts(parsed.path)
        return segments

    def _read_manuscript_section_segments(self, parsed: "refs.ManuscriptRef") -> list[str]:
        record_row = self._conn.execute("SELECT * FROM records WHERE ref = ?", (parsed.path,)).fetchone()
        if record_row is None:
            raise BadRef(f"no such record: {parsed.path}")
        paragraphs = tuple(self._paragraph_texts(parsed.path))
        section = _manuscript_section(paragraphs, parsed.heading, parsed.occurrence)
        header = self._read_header_line(record_row)
        return [header] + section

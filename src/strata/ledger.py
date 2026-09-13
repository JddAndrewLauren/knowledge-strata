"""The ledger: durable ids, exact-text anchors, retired text and the corpus
revision (ADR-0001, wayfinder #6, #15, #10).

One SQLite file, never dropped, holding everything that is history rather
than derivation. It knows nothing about folders, converters or search: it
takes a unit's path and hash and a version's paragraph list and returns
anchor numbers. Refs are parsed and rendered through ``strata.refs`` only;
this module never builds a ref string by hand.

Alignment is the one piece of real complexity: a paragraph keeps its anchor
only when its stored text is exactly equal to an old paragraph's (bytes of
the ``str``, no normalization); a hash accelerates the search but equality
decides. Equal duplicates pair first-unmatched-old to first-unmatched-new in
document order. Every unmatched old anchor retires and its exact text moves
to ``retired_text``; every unmatched new paragraph gets a fresh, never-reused
number. The whole update is one transaction.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from strata import refs

_ID_NUMBER = re.compile(r"SRC-(\d{6})")


def _format_id(number: int) -> str:
    return f"SRC-{number:06d}"


def _id_number(unit_id: str) -> int:
    match = _ID_NUMBER.fullmatch(unit_id)
    if match is None:
        raise ValueError(f"not a unit id: {unit_id!r}")
    return int(match.group(1))


@dataclass(frozen=True)
class AlignResult:
    """One unit's outcome from :meth:`Ledger.align` or :meth:`Ledger.retire_unit`.

    ``anchors`` gives the anchor number for each paragraph passed in, in the
    same order - the "returns anchor numbers" the ledger's brief promises.
    ``changed`` is false when the call was a no-op (unchanged content,
    converter and dating); the CLI prints ``line()`` only when it is true,
    since there is no other drift artifact (ADR-0001 amendment).
    """

    id: str
    version: int
    anchors: tuple[int, ...]
    kept: int
    retired: int
    added: int
    changed: bool

    def line(self) -> str:
        return f"{self.id}: v{self.version}, {self.kept} kept, {self.retired} retired, {self.added} added"


def _match_paragraphs(
    old_live: list[tuple[int, str]], new_paragraphs: Sequence[str]
) -> tuple[list[int | None], list[int]]:
    """Align one version to the next by exact stored text.

    ``old_live`` is ``[(anchor_id, text), ...]`` in current document order,
    live anchors only. Returns ``(assignment, retiring)``: ``assignment[i]``
    is the anchor id kept for ``new_paragraphs[i]``, or ``None`` for a
    paragraph that needs a fresh anchor; ``retiring`` lists the anchor ids
    from ``old_live`` that matched nothing. Equal duplicates pair
    first-unmatched-old to first-unmatched-new in document order because each
    text's candidates are queued in old document order and popped in new
    document order.
    """
    queues: dict[str, deque[int]] = {}
    order_of_id: list[int] = []
    for anchor_id, text in old_live:
        queues.setdefault(text, deque()).append(anchor_id)
        order_of_id.append(anchor_id)
    assignment: list[int | None] = []
    for text in new_paragraphs:
        queue = queues.get(text)
        if queue:
            assignment.append(queue.popleft())
        else:
            assignment.append(None)
    matched = {anchor_id for anchor_id in assignment if anchor_id is not None}
    retiring = [anchor_id for anchor_id in order_of_id if anchor_id not in matched]
    return assignment, retiring


_SCHEMA = """
CREATE TABLE IF NOT EXISTS units (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    sha256 TEXT,
    deleted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS versions (
    unit_id TEXT NOT NULL REFERENCES units(id),
    n INTEGER NOT NULL,
    converter TEXT,
    at TEXT,
    order_json TEXT NOT NULL,
    PRIMARY KEY (unit_id, n)
);
CREATE TABLE IF NOT EXISTS anchors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    unit_id TEXT NOT NULL REFERENCES units(id),
    p INTEGER NOT NULL,
    exact_text_hash TEXT NOT NULL,
    added_v INTEGER NOT NULL,
    retired_v INTEGER,
    UNIQUE (unit_id, p)
);
CREATE TABLE IF NOT EXISTS active_text (
    anchor_id INTEGER PRIMARY KEY REFERENCES anchors(id),
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS retired_text (
    anchor_id INTEGER PRIMARY KEY REFERENCES anchors(id),
    text TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS corpus_revision (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    token INTEGER NOT NULL
);
INSERT OR IGNORE INTO corpus_revision (id, token) VALUES (1, 0);
"""


class Ledger:
    """``.strata/ledger.db``. Durable, never dropped (ADR-0001)."""

    def __init__(self, path: str | Path):
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # -- registration ------------------------------------------------------

    def register(self, paths: Sequence[str]) -> dict[str, str]:
        """Ensure every path has a unit id, allocating ids for paths never
        seen before in sorted path order (ADR-0001) and never reusing a
        retired one. Idempotent: an already-known path (deleted or not)
        keeps its id."""
        with self._conn:
            existing = {row["path"]: row["id"] for row in self._conn.execute("SELECT path, id FROM units")}
            wanted = list(dict.fromkeys(paths))
            new_paths = sorted(path for path in wanted if path not in existing)
            if new_paths:
                next_number = self._next_unit_number()
                for offset, path in enumerate(new_paths):
                    unit_id = _format_id(next_number + offset)
                    self._conn.execute(
                        "INSERT INTO units (id, path, sha256, deleted) VALUES (?, ?, NULL, 0)",
                        (unit_id, path),
                    )
                    existing[path] = unit_id
            return {path: existing[path] for path in wanted}

    def _next_unit_number(self) -> int:
        rows = self._conn.execute("SELECT id FROM units").fetchall()
        return max((_id_number(row["id"]) for row in rows), default=0) + 1

    def known_units(self) -> dict[str, tuple[str, bool]]:
        """Every registered path, its unit id and its deleted flag - a
        caller's only way to notice that a path it once registered has
        vanished from the units it is walking this time (the sources
        adapter's deletion detection)."""
        return {
            row["path"]: (row["id"], bool(row["deleted"]))
            for row in self._conn.execute("SELECT path, id, deleted FROM units")
        }

    # -- alignment -----------------------------------------------------------

    def align(
        self,
        path: str,
        sha256: str,
        paragraphs: Sequence[str],
        *,
        converter: str = "",
        at: str = "",
        dated: bool = False,
    ) -> AlignResult:
        """Align ``paragraphs`` (a new version's text, in document order)
        against the unit at ``path``, previously registered with
        :meth:`register`. ``dated`` signals a dating-only change the ledger
        cannot see for itself (CONTEXT.md: corpus revision advances on it
        too) even when content and converter are unchanged."""
        paragraphs = list(paragraphs)
        with self._conn:
            row = self._conn.execute(
                "SELECT id, sha256, deleted FROM units WHERE path = ?", (path,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unit not registered: {path!r} - call register() first")
            unit_id = row["id"]
            last_n = self._last_version(unit_id)
            last_converter = self._version_converter(unit_id, last_n) if last_n else None
            unchanged = (
                last_n > 0
                and not row["deleted"]
                and row["sha256"] == sha256
                and last_converter == converter
                and not dated
            )
            if unchanged:
                order = self._order_json(unit_id, last_n)
                return AlignResult(unit_id, last_n, tuple(order), kept=len(order), retired=0, added=0, changed=False)
            return self._do_align(unit_id, last_n, paragraphs, sha256, converter, at)

    def _do_align(
        self,
        unit_id: str,
        last_n: int,
        paragraphs: list[str],
        sha256: str,
        converter: str,
        at: str,
    ) -> AlignResult:
        n = last_n + 1
        old_order = self._order_json(unit_id, last_n) if last_n else []
        live_rows = {
            row["p"]: (row["id"], row["text"])
            for row in self._conn.execute(
                "SELECT a.id AS id, a.p AS p, t.text AS text FROM anchors a "
                "JOIN active_text t ON t.anchor_id = a.id WHERE a.unit_id = ? AND a.retired_v IS NULL",
                (unit_id,),
            )
        }
        old_live = [(live_rows[p][0], live_rows[p][1]) for p in old_order]
        p_of_anchor_id = {anchor_id: p for p, (anchor_id, _) in live_rows.items()}
        text_of_anchor_id = {anchor_id: text for anchor_id, text in old_live}

        assignment, retiring = _match_paragraphs(old_live, paragraphs)

        next_p = self._conn.execute(
            "SELECT COALESCE(MAX(p), 0) FROM anchors WHERE unit_id = ?", (unit_id,)
        ).fetchone()[0]
        new_order: list[int] = []
        new_anchors: list[tuple[int, str]] = []  # (p, text) for fresh anchors, in order
        kept = 0
        for anchor_id, paragraph in zip(assignment, paragraphs):
            if anchor_id is not None:
                new_order.append(p_of_anchor_id[anchor_id])
                kept += 1
            else:
                next_p += 1
                new_order.append(next_p)
                new_anchors.append((next_p, paragraph))

        self._conn.execute(
            "INSERT INTO versions (unit_id, n, converter, at, order_json) VALUES (?, ?, ?, ?, ?)",
            (unit_id, n, converter, at, json.dumps(new_order)),
        )
        self._retire_anchors(retiring, text_of_anchor_id, n)
        self._add_anchors(unit_id, new_anchors, n)
        self._conn.execute(
            "UPDATE units SET sha256 = ?, deleted = 0 WHERE id = ?", (sha256, unit_id)
        )
        self._bump_revision()
        return AlignResult(
            unit_id,
            n,
            tuple(new_order),
            kept=kept,
            retired=len(retiring),
            added=len(new_anchors),
            changed=True,
        )

    def _retire_anchors(self, retiring: list[int], text_of: dict[int, str], n: int) -> None:
        for anchor_id in retiring:
            self._conn.execute("UPDATE anchors SET retired_v = ? WHERE id = ?", (n, anchor_id))
            self._write_retired_text(anchor_id, text_of[anchor_id])

    def _write_retired_text(self, anchor_id: int, text: str) -> None:
        """A separate step so a crash between the anchor update and the text
        write can never happen inside one statement (ADR-0001 amendment:
        retirement and version/anchor updates are atomic)."""
        self._conn.execute("INSERT INTO retired_text (anchor_id, text) VALUES (?, ?)", (anchor_id, text))
        self._conn.execute("DELETE FROM active_text WHERE anchor_id = ?", (anchor_id,))

    def _add_anchors(self, unit_id: str, new_anchors: list[tuple[int, str]], n: int) -> None:
        for p, text in new_anchors:
            cur = self._conn.execute(
                "INSERT INTO anchors (unit_id, p, exact_text_hash, added_v, retired_v) "
                "VALUES (?, ?, ?, ?, NULL)",
                (unit_id, p, _hash(text), n),
            )
            self._conn.execute(
                "INSERT INTO active_text (anchor_id, text) VALUES (?, ?)", (cur.lastrowid, text)
            )

    # -- deletion --------------------------------------------------------

    def retire_unit(self, path: str, *, at: str = "") -> AlignResult:
        """A raw unit has vanished: retire every live anchor with its exact
        text, mark the unit deleted, never remove its row or anchors
        (ADR-0001). A no-op (``changed=False``) if already deleted."""
        with self._conn:
            row = self._conn.execute(
                "SELECT id, deleted FROM units WHERE path = ?", (path,)
            ).fetchone()
            if row is None:
                raise KeyError(f"unit not registered: {path!r}")
            unit_id = row["id"]
            if row["deleted"]:
                last_n = self._last_version(unit_id)
                return AlignResult(unit_id, last_n, (), kept=0, retired=0, added=0, changed=False)
            last_n = self._last_version(unit_id)
            n = last_n + 1
            live = self._conn.execute(
                "SELECT a.id AS id, t.text AS text FROM anchors a "
                "JOIN active_text t ON t.anchor_id = a.id WHERE a.unit_id = ? AND a.retired_v IS NULL",
                (unit_id,),
            ).fetchall()
            text_of = {r["id"]: r["text"] for r in live}
            self._conn.execute(
                "INSERT INTO versions (unit_id, n, converter, at, order_json) VALUES (?, ?, NULL, ?, ?)",
                (unit_id, n, at, json.dumps([])),
            )
            self._retire_anchors(list(text_of), text_of, n)
            self._conn.execute("UPDATE units SET deleted = 1 WHERE id = ?", (unit_id,))
            self._bump_revision()
            return AlignResult(unit_id, n, (), kept=0, retired=len(text_of), added=0, changed=True)

    # -- reading -----------------------------------------------------------

    def text(self, ref: str) -> str:
        """Live text for a single paragraph anchor, or for a retired one the
        marker, its exact original text and the bare-record pointer (never a
        suggested replacement)."""
        parsed = refs.parse(ref)
        if not isinstance(parsed, refs.SourceRef) or parsed.anchor is None or parsed.end is not None or parsed.tail:
            raise ValueError(f"text() takes a single paragraph anchor, not {ref!r} - use range() for a span")
        unit_id = parsed.id
        row = self._conn.execute(
            "SELECT id, added_v, retired_v FROM anchors WHERE unit_id = ? AND p = ?",
            (unit_id, parsed.anchor),
        ).fetchone()
        if row is None:
            raise ValueError(f"no such anchor: {ref}")
        if row["retired_v"] is None:
            return self._conn.execute(
                "SELECT text FROM active_text WHERE anchor_id = ?", (row["id"],)
            ).fetchone()["text"]
        text = self._conn.execute(
            "SELECT text FROM retired_text WHERE anchor_id = ?", (row["id"],)
        ).fetchone()["text"]
        at = self._conn.execute(
            "SELECT at FROM versions WHERE unit_id = ? AND n = ?", (unit_id, row["retired_v"])
        ).fetchone()["at"]
        canonical = refs.render(parsed)
        marker = f"{canonical} - retired at v{row['retired_v']} ({at}); the v{row['added_v']} text it cited:"
        return f"{marker}\n{text}\nThe record's current text is {unit_id}."

    def live_anchors(self, unit_id: str) -> list[int]:
        """The current live anchor numbers for ``unit_id``, in document order
        (the latest version's paragraph order) - an id, not a position
        (ADR-0001). Read-only: the index module uses this to attach a durable
        anchor to each paragraph it indexes, on the assumption (the caller's
        to keep true) that the paragraphs it is given are the same list, in
        the same order, most recently passed to :meth:`align` for this unit.
        Empty for an unregistered or never-aligned unit."""
        last_n = self._last_version(unit_id)
        return self._order_json(unit_id, last_n) if last_n else []

    def range(self, ref: str) -> list[tuple[int, str]] | str:
        """Live anchors between two endpoints inclusive, in current document
        order regardless of numeric labels. A reversed range raises; a
        retired or missing endpoint returns a diagnostic string rather than
        a guessed range (ADR-0001)."""
        parsed = refs.parse(ref)
        if not isinstance(parsed, refs.SourceRef) or parsed.anchor is None or (parsed.end is None and not parsed.tail):
            raise ValueError(f"range() takes a span (p17-22 or p17-), not {ref!r}")
        unit_id = parsed.id
        last_n = self._last_version(unit_id)
        order = self._order_json(unit_id, last_n) if last_n else []

        start = self._locate(unit_id, order, parsed.anchor)
        if isinstance(start, str):
            return start
        if parsed.tail:
            end_pos = len(order) - 1
        else:
            end = self._locate(unit_id, order, parsed.end)
            if isinstance(end, str):
                return end
            end_pos = end
        if start > end_pos:
            raise ValueError(
                f"reversed range: {ref} - {refs.anchor(parsed.anchor)} comes after "
                f"{refs.anchor(order[end_pos])} in document order"
            )
        result = []
        for p in order[start : end_pos + 1]:
            _, text = self._live_anchor(unit_id, p)
            result.append((p, text))
        return result

    def _locate(self, unit_id: str, order: list[int], p: int) -> int | str:
        if p in order:
            return order.index(p)
        row = self._conn.execute(
            "SELECT retired_v FROM anchors WHERE unit_id = ? AND p = ?", (unit_id, p)
        ).fetchone()
        if row is None:
            return f"{unit_id} {refs.anchor(p)} does not exist"
        return f"{unit_id} {refs.anchor(p)} is retired; read it directly for its exact text"

    def _live_anchor(self, unit_id: str, p: int) -> tuple[int, str]:
        row = self._conn.execute(
            "SELECT a.id AS id, t.text AS text FROM anchors a JOIN active_text t ON t.anchor_id = a.id "
            "WHERE a.unit_id = ? AND a.p = ?",
            (unit_id, p),
        ).fetchone()
        return row["id"], row["text"]

    # -- corpus revision -----------------------------------------------------

    def corpus_revision(self) -> str:
        """An opaque, durable token that advances whenever a unit's content,
        membership, converter or dating changes (CONTEXT.md)."""
        row = self._conn.execute("SELECT token FROM corpus_revision WHERE id = 1").fetchone()
        return str(row["token"])

    def _bump_revision(self) -> None:
        self._conn.execute("UPDATE corpus_revision SET token = token + 1 WHERE id = 1")

    # -- internals -----------------------------------------------------------

    def _last_version(self, unit_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(n), 0) AS n FROM versions WHERE unit_id = ?", (unit_id,)
        ).fetchone()
        return row["n"]

    def _version_converter(self, unit_id: str, n: int) -> str | None:
        row = self._conn.execute(
            "SELECT converter FROM versions WHERE unit_id = ? AND n = ?", (unit_id, n)
        ).fetchone()
        return row["converter"] if row else None

    def _order_json(self, unit_id: str, n: int) -> list[int]:
        row = self._conn.execute(
            "SELECT order_json FROM versions WHERE unit_id = ? AND n = ?", (unit_id, n)
        ).fetchone()
        return json.loads(row["order_json"]) if row else []


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

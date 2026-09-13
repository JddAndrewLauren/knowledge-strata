"""strata.corpus.sources: the corpus roots in, kind ``source`` Records out
(design.md, "Sources adapter"; wayfinder #21).

Walks every corpus root in sorted path order, skipping dotfiles and anything
under a ``cache/`` directory. Each file is one raw unit; a unit's identity is
its root index plus its root-relative path (``register`` sorts by this, so
ids come out in sorted path order), stable across runs - a moved file is a
new unit, and two roots naming the same folder collapse to one before
walking so the same file is never counted twice.

For each unit the bytes are hashed and, unless the user-level conversion
cache at ``~/.strata/cache/store.db`` already holds this hash and converter,
handed to :mod:`strata.normalizer`; :mod:`strata.dating` reads the bytes and
the converter's paragraphs; :class:`strata.ledger.Ledger` gives the unit its
stable id and aligns the new paragraphs to a version. A unit the normalizer
refuses, or that converts to no text, gets no id and no Record - the raw
bytes are never worth a durable identity - and is counted in the sync report
instead. A previously registered path that produced no Record on this walk -
its file gone, or still there but now refused - has its unit retired: its
live anchors keep their exact text, retired, and the corpus revision
advances (CONTEXT.md: a membership change). The path keeps its id, so a
file that later converts again resumes its unit at a new version.

Never opens an attachment and never writes inside a corpus root.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from strata import dating, normalizer, refs
from strata.ledger import Ledger
from strata.record import Record


@dataclass(frozen=True)
class Skip:
    """One raw unit that produced no Record, and why: a refusal from the
    normalizer, "no text produced" for a pdf with no text layer, or an
    unclaimed suffix."""

    path: str
    reason: str


@dataclass(frozen=True)
class SyncReport:
    """One call's outcome: the Records to index, the units that produced
    none (and why), and the unit ids retired on this call - their file is
    gone, or it is still there but no longer converts (listed in ``skipped``
    too) - and so marked deleted in the ledger."""

    records: tuple[Record, ...]
    skipped: tuple[Skip, ...]
    deleted: tuple[str, ...]


def sync(roots: Sequence[str | Path], ledger: Ledger, *, cache_db: str | Path | None = None) -> SyncReport:
    """Walk ``roots``, align every convertible unit through ``ledger`` and
    return the Records to index plus a report of what was skipped and what
    was retired as deleted."""
    resolved_roots = _dedupe_roots(roots)
    units = _walk(resolved_roots)
    at = datetime.now(timezone.utc).isoformat()

    skipped: list[Skip] = []
    pending: list[tuple[str, str, bytes, str, str, tuple[str, ...], str]] = []

    with _ConversionCache(cache_db) as cache:
        for key, relative, path in units:
            converter = normalizer.CONVERTER_IDS.get(path.suffix.lower())
            if converter is None:
                skipped.append(Skip(relative, f"no converter claims the suffix {path.suffix.lower()!r}"))
                continue
            content = path.read_bytes()
            sha256 = hashlib.sha256(content).hexdigest()
            cached = cache.get(sha256, converter)
            if cached is None:
                converted = normalizer.normalize(content, relative)
                if isinstance(converted, normalizer.Refusal):
                    cache.put_skip(sha256, converter, converted.reason)
                    skipped.append(Skip(relative, converted.reason))
                    continue
                if not converted.paragraphs:
                    reason = converted.metadata.get("reason", "no text produced")
                    cache.put_skip(sha256, converter, reason)
                    skipped.append(Skip(relative, reason))
                    continue
                cache.put_ok(sha256, converter, converted.paragraphs, converted.title)
                paragraphs, title = converted.paragraphs, converted.title
            elif cached.skip_reason is not None:
                skipped.append(Skip(relative, cached.skip_reason))
                continue
            else:
                paragraphs, title = cached.paragraphs, cached.title
            pending.append((key, relative, content, sha256, converter, paragraphs, title))

    ids = ledger.register([key for key, *_ in pending])
    records = []
    for key, relative, content, sha256, converter, paragraphs, title in pending:
        when = dating.date(dating.RawUnit(path=relative, kind="source", content=content, paragraphs=paragraphs))
        ledger.align(key, sha256, paragraphs, converter=converter, at=at)
        ref = refs.render(refs.SourceRef(ids[key]))
        # An empty-Subject email with no body words converts to an empty
        # title (normalizer.py: #28's "then the ref" fallback); Record
        # rejects an empty title, so the ref itself stands in for it.
        records.append(
            Record(
                ref=ref, kind="source", date=when,
                title=title if title.strip() else ref,
                paragraphs=tuple(paragraphs),
            )
        )

    live = {key for key, *_ in pending}
    deleted = []
    for path, (unit_id, is_deleted) in ledger.known_units().items():
        if path not in live and not is_deleted:
            ledger.retire_unit(path, at=at)
            deleted.append(unit_id)

    return SyncReport(records=tuple(records), skipped=tuple(skipped), deleted=tuple(deleted))


def _dedupe_roots(roots: Sequence[str | Path]) -> list[Path]:
    """Resolved roots, first-listed order, duplicates dropped - two roots
    naming the same folder must walk it once, not twice."""
    seen: dict[Path, None] = {}
    for root in roots:
        seen.setdefault(Path(root).resolve(), None)
    return list(seen)


def _walk(roots: list[Path]) -> list[tuple[str, str, Path]]:
    """``(ledger key, corpus-relative path, absolute path)`` for every file
    under every root, in sorted key order. The key is the root index plus
    the relative path (a unit's identity across runs); the relative path
    alone is what the normalizer and the dating module see."""
    units = []
    for index, root in enumerate(roots):
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            parts = path.relative_to(root).parts
            if any(part.startswith(".") for part in parts):
                continue
            if "cache" in parts[:-1]:
                continue
            relative = "/".join(parts)
            units.append((f"{index:03d}/{relative}", relative, path))
    units.sort(key=lambda unit: unit[0])
    return units


def _default_cache_db() -> Path:
    return Path.home() / ".strata" / "cache" / "store.db"


@dataclass(frozen=True)
class _Cached:
    """One cache lookup's outcome: converted text, or why it was skipped."""

    paragraphs: tuple[str, ...] | None
    title: str | None
    skip_reason: str | None


class _ConversionCache:
    """``~/.strata/cache/store.db``'s ``conversions`` table, keyed by content
    hash and converter (design.md, "Multi-project"): normalizing a file is a
    function of immutable bytes, so a second sync - or a second project over
    the same archive - pays nothing for it. Disposable: everything under
    ``~/.strata/cache/`` costs only a reconversion to lose, never a citation
    (CONTEXT.md, "Cache").
    """

    def __init__(self, cache_db: str | Path | None):
        path = Path(cache_db) if cache_db is not None else _default_cache_db()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS conversions ("
            "sha256 TEXT NOT NULL, converter TEXT NOT NULL, "
            "paragraphs TEXT, title TEXT, skip_reason TEXT, "
            "PRIMARY KEY (sha256, converter))"
        )
        self._conn.commit()

    def __enter__(self) -> _ConversionCache:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._conn.close()

    def get(self, sha256: str, converter: str) -> _Cached | None:
        row = self._conn.execute(
            "SELECT paragraphs, title, skip_reason FROM conversions WHERE sha256 = ? AND converter = ?",
            (sha256, converter),
        ).fetchone()
        if row is None:
            return None
        paragraphs, title, skip_reason = row
        if skip_reason is not None:
            return _Cached(None, None, skip_reason)
        return _Cached(tuple(json.loads(paragraphs)), title, None)

    def put_ok(self, sha256: str, converter: str, paragraphs: tuple[str, ...], title: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO conversions (sha256, converter, paragraphs, title, skip_reason) "
                "VALUES (?, ?, ?, ?, NULL)",
                (sha256, converter, json.dumps(list(paragraphs)), title),
            )

    def put_skip(self, sha256: str, converter: str, reason: str) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO conversions (sha256, converter, paragraphs, title, skip_reason) "
                "VALUES (?, ?, NULL, NULL, ?)",
                (sha256, converter, reason),
            )

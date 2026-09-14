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

The converter id in the cache key and passed to ``Ledger.align`` carries its
hand-bumped version (``eml@1``, :func:`strata.normalizer.converter_id`), so
bumping it alone misses the cache and re-converts. Once every unit is aligned
under the current id, the cache sweeps the rows a bump - or a bare pre-#45
converter name - left behind (issue #54); a sync with no bump sweeps nothing.
The ledger remembers the dating ruleset version it last aligned under (issue
#45); on the one sync where :data:`strata.dating.DATING_VERSION` has moved,
every unit is passed ``dated=True`` so it gets a new version without a
reconversion.
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
from strata.ledger import AlignResult, Ledger
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
    too) - and so marked deleted in the ledger. ``aligned`` is every unit's
    :class:`~strata.ledger.AlignResult` from this call, live and retired
    alike, in walk order - the CLI's ``strata index`` prints ``line()`` for
    the ones where ``changed`` (CONTEXT.md, "Drift"; ADR-0001)."""

    records: tuple[Record, ...]
    skipped: tuple[Skip, ...]
    deleted: tuple[str, ...]
    aligned: tuple[AlignResult, ...] = ()


def sync(roots: Sequence[str | Path], ledger: Ledger, *, cache_db: str | Path | None = None) -> SyncReport:
    """Walk ``roots``, align every convertible unit through ``ledger`` and
    return the Records to index plus a report of what was skipped and what
    was retired as deleted."""
    resolved_roots = _dedupe_roots(roots)
    units = _walk(resolved_roots)
    at = datetime.now(timezone.utc).isoformat()

    # A ledger with no stored dating version - never recorded, before this
    # feature or fresh - treats the current constant as already applied: it
    # is written below with no unit re-dated. Only a value that has actually
    # moved forces every unit's dating to count as changed this sync
    # (CONTEXT.md, "Corpus revision").
    stored_dating_version = ledger.dating_version()
    dated = stored_dating_version is not None and stored_dating_version != dating.DATING_VERSION

    skipped: list[Skip] = []
    pending: list[tuple[str, str, bytes, str, str, tuple[str, ...], str]] = []
    current_converters: dict[str, str] = {}  # converter name -> the id used this sync

    with _ConversionCache(cache_db) as cache:
        for key, relative, path in units:
            converter = normalizer.converter_id(path.suffix.lower())
            if converter is None:
                skipped.append(Skip(relative, f"no converter claims the suffix {path.suffix.lower()!r}"))
                continue
            current_converters[converter.split("@", 1)[0]] = converter
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

        # Every unit's conversion is cached above under its current converter
        # id; only now is it safe to sweep the rows a bumped converter (or a
        # bare pre-#45 name) left behind, so an interrupted sync never leaves
        # the cache with neither generation.
        cache.sweep(current_converters)

    ids = ledger.register([key for key, *_ in pending])
    records = []
    aligned: list[AlignResult] = []
    for key, relative, content, sha256, converter, paragraphs, title in pending:
        when = dating.date(dating.RawUnit(path=relative, kind="source", content=content, paragraphs=paragraphs))
        aligned.append(ledger.align(key, sha256, paragraphs, converter=converter, at=at, dated=dated))
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
            aligned.append(ledger.retire_unit(path, at=at))
            deleted.append(unit_id)

    if stored_dating_version != dating.DATING_VERSION:
        ledger.set_dating_version(dating.DATING_VERSION)

    return SyncReport(records=tuple(records), skipped=tuple(skipped), deleted=tuple(deleted), aligned=tuple(aligned))


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
        # The converter id (``text@2``) this cache last swept ``conversions``
        # for, one row per converter name - how a bump is told apart from an
        # unchanged sync without re-deriving it from the rows themselves.
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS converter_generations (name TEXT PRIMARY KEY, converter TEXT NOT NULL)"
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

    def sweep(self, current: dict[str, str]) -> None:
        """Delete rows left behind by a superseded converter id, for every
        converter name used this sync (``current``: name -> the id, e.g.
        ``{"text": "text@2"}``). A name's superseded keys are its bare
        pre-#45 name and any version other than ``current``; a name whose id
        matches what was swept for last time is untouched, so an unbumped
        converter, or a second sync at the same version, deletes nothing."""
        with self._conn:
            for name, converter in current.items():
                row = self._conn.execute(
                    "SELECT converter FROM converter_generations WHERE name = ?", (name,)
                ).fetchone()
                if row is not None and row[0] == converter:
                    continue
                self._conn.execute(
                    "DELETE FROM conversions WHERE converter = ? OR (converter LIKE ? AND converter != ?)",
                    (name, f"{name}@%", converter),
                )
                self._conn.execute(
                    "INSERT OR REPLACE INTO converter_generations (name, converter) VALUES (?, ?)",
                    (name, converter),
                )

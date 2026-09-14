"""strata.project: one project folder (CONTEXT.md "Project") and the
fresh-as-of-this-call sync over it (design.md "Freshness") that
:mod:`strata.server` runs before every tool call and :mod:`strata.cli` runs
by hand for ``strata init`` and ``strata index``.

The walk is the same for both: resolve the corpus roots from the config,
hand them to the sources adapter, add the ``notes`` folder and the
manuscript, and give every Record to :meth:`~strata.index.Index.sync`. So
is the failure rule: a failure partway hands whatever Records were already
collected to ``index.sync(..., complete=False)`` - real, ledger-durable
progress, not just a flag - unless the index was already complete, because
one bad refresh must not retract a project whose first index has already
finished (design.md "Indexing state" is about a *first* index; a transient
failure later just leaves the last-known-good, still-complete state). What
differs is only what each caller prints about it, so nothing here prints.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from strata import config
from strata.corpus import manuscript, notes, sources
from strata.embeddings import Embedder
from strata.index import Index
from strata.ledger import Ledger
from strata.record import Record


@dataclass(frozen=True)
class Project:
    """One project folder's fixed identity: paths plus the one long-lived,
    thread-safe collaborator (the embedder). ``Ledger`` and ``Index`` hold a
    ``sqlite3.Connection`` each, so they are opened fresh per sync
    (:func:`open_index`) instead of living here. ``cache_db`` overrides the
    conversion cache (``~/.strata/cache/store.db`` when ``None``); tests
    point it at a temporary file so the hermetic suite never touches a real
    home directory. The embedder's model files live beside that cache, at
    ``~/.strata/cache/models/`` (:class:`~strata.embeddings.FastEmbedEmbedder`).
    """

    folder: Path
    embedder: Embedder
    cache_db: Path | None = None

    @property
    def ledger_path(self) -> Path:
        return self.folder / ".strata" / "ledger.db"

    @property
    def index_path(self) -> Path:
        return self.folder / ".strata" / "cache" / "index.db"


@dataclass(frozen=True)
class SyncOutcome:
    """What one :func:`sync` did: the Records handed to the index, the
    sources adapter's report (``None`` if the walk failed before it
    returned), the failure if any, and whether the index was already
    complete when the sync began - the fact the callers' messages turn on."""

    records: tuple[Record, ...]
    source_report: sources.SyncReport | None
    error: Exception | None
    was_complete: bool


def open_index(project: Project, cfg: config.ProjectConfig) -> tuple[Ledger, Index]:
    """A fresh ``Ledger`` and ``Index`` over ``project``; the caller closes
    both when its call is done."""
    ledger = Ledger(project.ledger_path)
    project.index_path.parent.mkdir(parents=True, exist_ok=True)
    index = Index(project.index_path, ledger=ledger, embedder=project.embedder, chunk_tokens=cfg.chunk_tokens)
    return ledger, index


def sync(project: Project, cfg: config.ProjectConfig, ledger: Ledger, index: Index) -> SyncOutcome:
    """Walk the corpus roots and the ``notes``/manuscript folders and hand
    every Record to ``index.sync`` - "fresh as of this call". See the module
    docstring for the failure rule."""
    was_complete = index.indexing_state == "complete"
    records: list[Record] = []
    source_report: sources.SyncReport | None = None
    try:
        source_report = sources.sync(cfg.corpus_roots(project.folder), ledger, cache_db=project.cache_db)
        records.extend(source_report.records)
        notes_folder = project.folder / "notes"
        if notes_folder.is_dir():
            records.extend(notes.read(notes_folder))
        manuscript_path = cfg.manuscript_path(project.folder)
        if manuscript_path is not None:
            records.extend(manuscript.read(manuscript_path).records)
    except Exception as error:
        if not was_complete:
            index.sync(records, complete=False)
        return SyncOutcome(tuple(records), source_report, error, was_complete)
    index.sync(records, complete=True)
    return SyncOutcome(tuple(records), source_report, None, was_complete)

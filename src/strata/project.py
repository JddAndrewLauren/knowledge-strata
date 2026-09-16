"""Project configuration and serialized refresh-before-read runtime."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import os
import threading
from strata import config as project_config

from strata.corpus import sources, notes, manuscript
from strata.embeddings import CachedEmbedder, FastEmbedEmbedder
from strata.index import Index
from strata.ledger import Ledger


class RefreshFailed(RuntimeError):
    """The last snapshot must not be presented as current."""


def config(project: Path) -> dict:
    value = project_config.load(project)
    return {'corpus': list(value.corpus), 'manuscript': value.manuscript,
            'chunk_tokens': value.chunk_tokens}


def resolve(project: Path, path: str) -> Path:
    return (project / path).resolve()


@contextmanager
def project_lock(project: Path):
    """OS locks are released on process exit, including interrupted refreshes."""
    lock = project / '.strata' / 'refresh.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            if stream.tell() == 0:
                stream.write(b'0'); stream.flush()
            stream.seek(0)
            # LK_LOCK has a short fixed retry limit; explicitly retry contention.
            import time
            while True:
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError as error:
                    if error.errno not in (13, 36):
                        raise
                    time.sleep(0.1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


class Project:
    def __init__(self, path: str | Path | None = None, *, folder=None, embedder=None, cache_db=None, cache_dir: str | Path | None = None,
                 embedder_factory=None, semantic=True, progress=None):
        self.path = Path(path if path is not None else folder).resolve()
        self.folder = self.path
        self.cache = Path(cache_dir or os.environ.get('STRATA_CACHE_DIR') or Path.home() / '.strata' / 'cache')
        self.cache_db = Path(cache_db) if cache_db is not None else self.cache / "store.db"
        self.factory = (lambda: embedder) if embedder is not None else embedder_factory or (lambda: FastEmbedEmbedder(cache_dir=self.cache / 'models'))
        self.semantic = semantic
        self.progress = progress or (lambda message: None)
        self._embedder = None
        self._lock = threading.RLock()

    @property
    def ledger_path(self):
        return self.path / '.strata' / 'ledger.db'

    @property
    def index_path(self):
        return self.path / '.strata' / 'cache' / 'index.db'

    @contextmanager
    def current(self, *, legacy_roots=None):
        with self._lock, project_lock(self.path):
            settings = config(self.path)
            cache = self.path / '.strata' / 'cache'
            cache.mkdir(parents=True, exist_ok=True)
            ledger = Ledger(self.path / '.strata' / 'ledger.db')
            index = None
            try:
                if self._embedder is None:
                    self.progress('Loading embedding model; first use may download model files. Retry strata index if interrupted.')
                    self._embedder = CachedEmbedder(self.factory(), self.cache_db)
                index = Index(cache / 'index.db', ledger=ledger, embedder=self._embedder,
                              semantic=self.semantic, chunk_tokens=settings.get('chunk_tokens', 80_000),
                              reply_token_budget=7980)
                # Persist incomplete status before any potentially failing work.
                self.was_complete = index.indexing_state == "complete"
                index.mark_complete(False)
                roots = [resolve(self.path, root) for root in settings['corpus']]
                report = sources.sync(roots, ledger, cache_db=self.cache_db,
                                      legacy_roots=legacy_roots, progress=self.progress)
                records = list(report.records) + list(notes.read(self.path / 'notes'))
                if settings.get('manuscript'):
                    folder = resolve(self.path, settings['manuscript'])
                    if not folder.is_dir():
                        raise OSError(f'manuscript folder is unavailable: {folder}')
                    records.extend(manuscript.read(folder).records)
                self.progress(f'Indexing {len(records)} records')
                index.sync(records)
                self.report = report
                self.progress(f'Index complete: {len(records)} records, {len(report.skipped)} skips, {len(report.deleted)} retired sources')
            except BaseException as error:
                if index:
                    index.mark_complete(False)
                    index.close()
                ledger.close()
                if isinstance(error, (KeyboardInterrupt, SystemExit)):
                    raise
                raise RefreshFailed(f'indexing: incomplete; refresh failed: {error}. Fix the cause and retry; no current results were served.') from error
            try:
                yield index
            finally:
                index.close()
                ledger.close()

    def refresh(self, *, legacy_roots=None):
        with self.current(legacy_roots=legacy_roots) as index:
            return {'records': len(self.report.records), 'skipped': len(self.report.skipped),
                    'corpus_revision': index._ledger.corpus_revision()}

    def search(self, **arguments):
        with self.current() as index:
            return index.search(**arguments)

    def read(self, **arguments):
        with self.current() as index:
            return index.read(**arguments)

"""Project configuration and serialized refresh-before-read runtime."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import os
import errno
import threading
import time
import sqlite3
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
def project_lock(project: Path, *, timeout: float = 2.0):
    """Bound contention; OS locks release on exit, including interrupted refreshes."""
    lock = project / '.strata' / 'refresh.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    with lock.open('a+b') as stream:
        if os.name == 'nt':
            import msvcrt
            if stream.tell() == 0:
                stream.write(b'0'); stream.flush()
            stream.seek(0)
        else:
            import fcntl
        while True:
            try:
                if os.name == 'nt':
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as error:
                if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if time.monotonic() >= deadline:
                    raise RefreshFailed('indexing: incomplete; refresh in progress; retry shortly') from error
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        try:
            yield
        finally:
            if os.name == 'nt':
                stream.seek(0); msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


class Project:
    def __init__(self, path: str | Path | None = None, *, folder=None, embedder=None, cache_db=None, cache_dir: str | Path | None = None,
                 embedder_factory=None, semantic=True, progress=None, lock_timeout=2.0):
        self.path = Path(path if path is not None else folder).resolve()
        self.folder = self.path
        self.cache = Path(cache_dir or os.environ.get('STRATA_CACHE_DIR') or Path.home() / '.strata' / 'cache')
        self.cache_db = Path(cache_db) if cache_db is not None else self.cache / "store.db"
        self.factory = (lambda: embedder) if embedder is not None else embedder_factory or (lambda: FastEmbedEmbedder(cache_dir=self.cache / 'models'))
        self.semantic = semantic
        self.progress = progress or (lambda message: None)
        self._embedder = None
        self._lock = threading.RLock()
        self.lock_timeout = lock_timeout

    @property
    def ledger_path(self):
        return self.path / '.strata' / 'ledger.db'

    @property
    def index_path(self):
        return self.path / '.strata' / 'cache' / 'index.db'

    @contextmanager
    def _refresh_lock(self):
        deadline = time.monotonic() + self.lock_timeout
        if not self._lock.acquire(timeout=self.lock_timeout):
            raise RefreshFailed('indexing: incomplete; refresh in progress; retry shortly')
        try:
            with project_lock(self.path, timeout=max(0, deadline - time.monotonic())):
                yield
        finally:
            self._lock.release()

    @contextmanager
    def current(self, *, legacy_roots=None):
        with self._refresh_lock():
            settings = config(self.path)
            cache = self.path / '.strata' / 'cache'
            cache.mkdir(parents=True, exist_ok=True)
            ledger = Ledger(self.path / '.strata' / 'ledger.db')
            index = None
            try:
                self.was_complete = False
                if self.index_path.exists():
                    connection = sqlite3.connect(self.index_path)
                    try:
                        row = connection.execute("SELECT value FROM meta WHERE key='indexing'").fetchone()
                        self.was_complete = bool(row and row[0] == 'complete')
                    except sqlite3.OperationalError:
                        pass  # A partial index still needs model/recovery progress.
                    finally:
                        connection.close()
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

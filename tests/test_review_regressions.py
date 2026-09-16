"""PR #64 review: setup, migration, missing entries, and shared-cache contention."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import sqlite3
import threading

import pytest
import yaml

from strata import cli
from strata.corpus import sources
from strata.embeddings import CachedEmbedder, FakeEmbedder
from strata.index import CursorError, Index
from strata.ledger import Ledger
from strata.project import Project, RefreshFailed, project_lock
from factories import make_note, make_source


@pytest.mark.parametrize('missing_at', ['stat', 'read_bytes'])
def test_disappearing_entry_is_reported_without_aborting(tmp_path, monkeypatch, missing_at):
    root = tmp_path / 'archive'; root.mkdir()
    gone = root / 'gone.txt'; gone.write_text('Disappearing evidence.')
    (root / 'good.txt').write_text('Still here.')
    original = getattr(Path, missing_at)
    def vanished(path, *args, **kwargs):
        if path == gone:
            raise FileNotFoundError('removed during scan')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, missing_at, vanished)
    ledger = Ledger(tmp_path / 'ledger.db')
    try:
        report = sources.sync([root], ledger, cache_db=tmp_path / 'store.db')
        assert len(report.records) == 1
        assert report.skipped[0].path == 'gone.txt'
        assert 'disappeared' in report.skipped[0].reason
    finally:
        ledger.close()


def test_dangling_symlink_does_not_block_archive(tmp_path):
    root = tmp_path / 'archive'; root.mkdir()
    (root / 'broken.txt').symlink_to(root / 'absent.txt')
    (root / 'good.txt').write_text('Available evidence.')
    ledger = Ledger(tmp_path / 'ledger.db')
    try:
        result = sources.sync([root], ledger, cache_db=tmp_path / 'store.db')
        assert len(result.records) == 1
        assert result.skipped == (sources.Skip('broken.txt', 'dangling link or file disappeared during scan'),)
    finally:
        ledger.close()


@pytest.mark.parametrize('failure', ['missing', 'inside', 'manuscript', 'mcp', 'permissions'])
def test_invalid_reinit_preserves_config_bytes(tmp_path, monkeypatch, failure):
    root = tmp_path / 'project'; archive = tmp_path / 'archive'; archive.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    cli.initialize(root, corpus=[str(archive)])
    config_path = root / '.strata/config.yaml'
    config_path.write_text(config_path.read_text() + 'chunk_tokens: 1234\n')
    before = config_path.read_bytes()
    corpus, manuscript = [str(archive)], None
    if failure == 'missing':
        corpus = [str(tmp_path / 'typo')]
    elif failure == 'inside':
        corpus = [str(tmp_path)]
    elif failure == 'manuscript':
        manuscript = str(tmp_path / 'missing-manuscript')
    elif failure == 'mcp':
        (root / '.mcp.json').write_text('{"mcpServers": []}')
    else:
        (root / '.claude/settings.json').write_text('{"permissions": []}')
    with pytest.raises(ValueError):
        cli.cmd_init(root, corpus=corpus, manuscript=manuscript, embedder=FakeEmbedder())
    assert config_path.read_bytes() == before


def test_valid_reinit_preserves_chunk_budget(tmp_path, monkeypatch):
    root = tmp_path / 'project'; archive = tmp_path / 'archive'; archive.mkdir()
    monkeypatch.setattr(Path, 'home', lambda: tmp_path / 'home')
    cli.initialize(root, corpus=[str(archive)])
    config_path = root / '.strata/config.yaml'
    config_path.write_text(config_path.read_text() + 'chunk_tokens: 1234\n')
    assert cli.cmd_init(root, corpus=[str(archive)], manuscript=None, embedder=FakeEmbedder()) == 0
    assert yaml.safe_load(config_path.read_text())['chunk_tokens'] == 1234


def test_detectably_reversed_legacy_roots_fail_without_retirement(tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'; a.mkdir(); b.mkdir()
    (a / 'alpha.txt').write_text('Alpha.'); (b / 'beta.txt').write_text('Beta.')
    ledger = Ledger(tmp_path / 'ledger.db')
    try:
        first = make_source(ledger, '000/alpha.txt', ['Alpha.'])
        second = make_source(ledger, '001/beta.txt', ['Beta.'])
        before = ledger.known_units(); revision = ledger.corpus_revision()
        with pytest.raises(ValueError, match='mapping conflicts'):
            sources.sync([a, b], ledger, legacy_roots=[b, a], cache_db=tmp_path / 'store.db')
        assert ledger.known_units() == before
        assert ledger.corpus_revision() == revision
        report = sources.sync([a, b], ledger, legacy_roots=[a, b], cache_db=tmp_path / 'store.db')
        assert {r.ref for r in report.records} == {first.ref, second.ref}
    finally:
        ledger.close()


def test_nested_legacy_roots_keep_both_existing_identities(tmp_path):
    root = tmp_path / 'archive'; inner = root / 'sub'; inner.mkdir(parents=True)
    (inner / 'x.txt').write_text('Evidence.')
    ledger = Ledger(tmp_path / 'ledger.db')
    try:
        first = make_source(ledger, '000/sub/x.txt', ['Evidence.'])
        second = make_source(ledger, '001/x.txt', ['Evidence.'])
        result = sources.sync([root, inner], ledger, legacy_roots=[root, inner], cache_db=tmp_path / 'store.db')
        assert {r.ref for r in result.records} == {first.ref, second.ref}
        assert not result.deleted
    finally:
        ledger.close()


def test_cache_writer_can_commit_during_second_embedding_batch(tmp_path):
    path = tmp_path / 'store.db'
    class ConcurrentWriter(FakeEmbedder):
        calls = 0
        def embed_passages(self, texts):
            self.calls += 1
            if self.calls == 2:
                # Another project's conversion write must not wait for this model.
                connection = sqlite3.connect(path, timeout=0.1)
                try:
                    with connection:
                        connection.execute('CREATE TABLE other_project (value TEXT)')
                        connection.execute("INSERT INTO other_project VALUES ('converted')")
                finally:
                    connection.close()
            return super().embed_passages(texts)
    model = ConcurrentWriter()
    cached = CachedEmbedder(model, path)
    assert len(cached.embed_passages([str(i) for i in range(65)])) == 65
    assert model.calls == 2


@pytest.mark.parametrize('same_instance', [True, False])
def test_busy_refresh_returns_incomplete_then_can_retry(tmp_path, same_instance):
    project = Project(tmp_path, embedder=FakeEmbedder(), lock_timeout=0.05)
    other = project if same_instance else Project(tmp_path, embedder=FakeEmbedder(), lock_timeout=0.05)
    held, release = threading.Event(), threading.Event()
    def hold():
        with project._refresh_lock():
            held.set()
            assert release.wait(5)
    with ThreadPoolExecutor() as executor:
        future = executor.submit(hold)
        assert held.wait(5)
        try:
            with pytest.raises(RefreshFailed, match='incomplete; refresh in progress'):
                with other._refresh_lock():
                    pytest.fail('contended lock acquired')
        finally:
            release.set()
        future.result()
    with other._refresh_lock():
        pass
    with project_lock(tmp_path):
        pass


def test_expired_long_read_handles_are_swept(tmp_path):
    ledger = Ledger(tmp_path / 'ledger.db')
    index = Index(tmp_path / 'index.db', ledger=ledger, embedder=FakeEmbedder(), semantic=False)
    try:
        note = make_note('notes/' + 'a/' * 3000 + 'item.md', ['Evidence.'])
        index.sync([note]); page = index.read(note.ref)
        assert page.continuation
        assert index._conn.execute("SELECT count(*) FROM meta WHERE key GLOB 'read_ref:*'").fetchone()[0] == 1
        index.sync([replace(note, paragraphs=('Changed.',))])
        assert index._conn.execute("SELECT count(*) FROM meta WHERE key GLOB 'read_ref:*'").fetchone()[0] == 0
        with pytest.raises(CursorError):
            index.read(cursor=page.continuation)
    finally:
        index.close(); ledger.close()


@pytest.mark.parametrize('error', [PermissionError('denied'), OSError(5, 'I/O error')])
def test_file_access_errors_still_abort_without_retirement(tmp_path, monkeypatch, error):
    root = tmp_path / 'archive'; root.mkdir()
    entry = root / 'entry.txt'; entry.write_text('Evidence.')
    ledger = Ledger(tmp_path / 'ledger.db')
    try:
        sources.sync([root], ledger, cache_db=tmp_path / 'store.db')
        before = ledger.known_units(); revision = ledger.corpus_revision()
        original = Path.stat
        def fail(path, *args, **kwargs):
            if path == entry:
                raise error
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'stat', fail)
        with pytest.raises(type(error)):
            sources.sync([root], ledger, cache_db=tmp_path / 'store.db')
        assert ledger.known_units() == before
        assert ledger.corpus_revision() == revision
    finally:
        ledger.close()


def test_model_progress_precedes_factory_on_first_refresh(tmp_path):
    root = tmp_path / 'project'; archive = tmp_path / 'archive'; archive.mkdir()
    cli.initialize(root, corpus=[str(archive)], host_home=tmp_path / 'home')
    messages = []
    def factory():
        assert any('Loading embedding model' in message for message in messages)
        assert project.was_complete is False
        return FakeEmbedder()
    project = Project(root, embedder_factory=factory, cache_dir=tmp_path / 'cache', progress=messages.append)
    project.refresh()

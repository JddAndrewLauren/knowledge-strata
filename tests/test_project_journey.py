"""The real adapters/runtime and MCP transport, with no downloaded model."""
import asyncio
from datetime import timedelta
import json
from pathlib import Path
import shutil
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from strata.cli import initialize
from strata.embeddings import CachedEmbedder, FakeEmbedder
from strata.project import Project, RefreshFailed
from strata.index import CursorError


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'project'; corpus = tmp_path / 'archive'; corpus.mkdir()
    (corpus / '2001-06-01.txt').write_text('2001-06-01\n\nPriya confirmed the cutoff was eleven oclock.\n\n' + 'Details. ' * 8000)
    initialize(root, corpus=[str(corpus)], host_home=tmp_path / 'host')
    return root, corpus


def runtime(root):
    return Project(root, cache_dir=root.parent / 'user-cache', embedder_factory=FakeEmbedder, semantic=False)


def test_init_preserves_configuration_and_artifacts(tmp_path):
    root = tmp_path / 'project'; root.mkdir(); corpus = tmp_path / 'archive'; corpus.mkdir()
    (root / '.mcp.json').write_text(json.dumps({'mcpServers': {'other': {'command': 'other'}}}))
    (root / '.claude').mkdir()
    (root / '.claude' / 'settings.json').write_text(json.dumps({'permissions': {'deny': ['Bash(rm *)']}, 'theme': 'dark'}))
    initialize(root, corpus=[str(corpus)], host_home=tmp_path / 'host')
    overview = root / 'notes' / 'project.md'; overview.write_text('# Saved finding\nDo not overwrite.')
    initialize(root, host_home=tmp_path / 'host')
    assert 'Do not overwrite' in overview.read_text()
    assert json.loads((root / '.mcp.json').read_text())['mcpServers']['other']['command'] == 'other'
    settings = json.loads((root / '.claude' / 'settings.json').read_text())
    assert settings['permissions']['deny'] == ['Bash(rm *)']
    assert settings['theme'] == 'dark'
    assert settings['permissions']['allow'].count('mcp__strata__read') == 1
    assert (tmp_path / 'host' / 'skills' / 'strata' / 'SKILL.md').is_file()


def test_finding_restart_exact_citation_edit_and_recovery(project):
    root, corpus = project; app = runtime(root)
    result = app.search(query='cutoff', kind='source')
    citation = result.hits[0].ref
    assert app.read(ref=citation).body == 'Priya confirmed the cutoff was eleven oclock.'
    revision = result.corpus_revision
    (root / 'notes' / 'project.md').write_text(f'# Project\n\nCutoff: eleven oclock. ({citation})\n\nUnfinished: verify the July change.')
    restarted = runtime(root)
    saved = restarted.read(ref='notes/project.md').body
    assert citation in saved and 'Unfinished' in saved
    assert restarted.search(query='cutoff').corpus_revision == revision
    file = corpus / '2001-06-01.txt'
    file.write_text(file.read_text().replace('eleven oclock', 'noon'))
    assert restarted.search(query='cutoff').corpus_revision != revision
    retired = restarted.read(ref=citation)
    assert 'eleven oclock' in retired.body
    assert 'retired' in retired.text()
    assert 'noon' in restarted.read(ref=restarted.search(query='cutoff', kind='source').hits[0].ref).body


def test_missing_archive_fails_closed_then_recovers(project):
    root, corpus = project; app = runtime(root)
    first = app.search(kind='source')
    moved = corpus.with_name('temporarily-offline'); corpus.rename(moved)
    with pytest.raises(RefreshFailed, match='no current results'):
        app.search()
    moved.rename(corpus)
    assert app.search(kind='source').corpus_revision == first.corpus_revision


def test_note_save_invalidates_pending_cursor_without_changing_corpus(project):
    root, _ = project; app = runtime(root)
    hit = app.search(kind='source').hits[0]
    page = app.read(ref=hit.ref.split(' ')[0])
    assert page.continuation
    before = app.search().corpus_revision
    (root / 'notes' / 'project.md').write_text('# Project\nSaved batch.')
    with pytest.raises(CursorError):
        app.read(cursor=page.continuation)
    assert app.search().corpus_revision == before


def test_shared_embedding_cache_survives_project_rebuild(tmp_path):
    first = FakeEmbedder({'evidence': 'topic'})
    CachedEmbedder(first, tmp_path / 'store.db').embed_passages(['evidence', 'evidence'])
    assert first.passage_calls == ['evidence']
    second = FakeEmbedder({'evidence': 'topic'})
    assert CachedEmbedder(second, tmp_path / 'store.db').embed_passages(['evidence'])
    assert second.passage_calls == []
    second.model_id = 'changed-model'
    CachedEmbedder(second, tmp_path / 'store.db').embed_passages(['evidence'])
    assert second.passage_calls == ['evidence']


def test_west_desk_can_be_read_with_real_ledger_citations(tmp_path):
    fixture = Path(__file__).resolve().parents[1] / 'examples' / 'west-desk'
    root = tmp_path / 'project'; shutil.copytree(fixture, root)
    initialize(root, corpus=['sources'], manuscript='manuscript', host_home=tmp_path / 'host')
    app = runtime(root)
    hits = app.search(query='cutoff', kind='source').hits
    assert hits
    for hit in hits:
        assert app.read(ref=hit.ref).body
    assert app.read(ref='manuscript/ch01-the-desk.md # The room').body
    # Persist actual citations, never treat illustrative fixture IDs as evidence.
    citation = hits[0].ref
    (root / 'notes' / 'verified.md').write_text(f'# Verified\n\nEvidence checked at {citation}.')
    assert citation in runtime(root).read(ref='notes/verified.md').body


def test_mcp_stdio_initialize_tools_read_and_errors(project):
    root, corpus = project
    script = ('from strata.server import run; from strata.project import Project; '
              'from strata.embeddings import FakeEmbedder; '
              'import sys; run(Project(sys.argv[1], cache_dir=sys.argv[2], '
              'embedder_factory=FakeEmbedder, semantic=False))')
    async def journey():
        params = StdioServerParameters(command=sys.executable,
            args=['-c', script, str(root), str(root.parent / 'mcp-cache')],
            env={'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src')})
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=15)) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                assert {tool.name for tool in tools} == {'search', 'read'}
                assert 'from' in tools[0].inputSchema['properties']
                result = await session.call_tool('search', {'query': 'cutoff', 'from': '2001-06', 'kind': 'source'})
                assert not result.isError and 'SRC-' in result.content[0].text
                result = await session.call_tool('read', {'ref': 'notes/project.md'})
                assert not result.isError and 'indexing: complete' in result.content[0].text
                invalid = await session.call_tool('search', {'extra': True})
                assert invalid.isError
                corpus.rename(corpus.with_name('offline'))
                failed = await session.call_tool('read', {'ref': 'notes/project.md'})
                assert failed.isError and 'incomplete' in failed.content[0].text
    asyncio.run(journey())


def test_runtime_reconciles_ledger_after_embedding_failure(project, monkeypatch):
    root, corpus = project
    embedder = FakeEmbedder()
    app = Project(root, cache_dir=root.parent / 'cache', embedder_factory=lambda: embedder)
    old = app.search(query='cutoff', kind='source').hits[0].ref
    original = embedder.embed_passages
    def fail(texts):
        raise RuntimeError('model failure')
    monkeypatch.setattr(embedder, 'embed_passages', fail)
    (corpus / '2001-06-01.txt').write_text('2001-06-01\n\nNew cutoff: noon.')
    with pytest.raises(RefreshFailed):
        app.search(query='cutoff')
    monkeypatch.setattr(embedder, 'embed_passages', original)
    assert 'New cutoff' in app.read(ref=app.search(query='cutoff', kind='source').hits[0].ref).body
    assert 'eleven oclock' in app.read(ref=old).body


def test_cli_init_and_index_use_the_runtime(tmp_path, monkeypatch, capsys):
    from strata import cli
    corpus = tmp_path / 'archive'; corpus.mkdir()
    (corpus / 'entry.txt').write_text('Evidence.')
    root = tmp_path / 'project'
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path / 'home'))
    monkeypatch.setattr(cli, 'Project', lambda path, progress=None: Project(
        path, cache_dir=tmp_path / 'cache', embedder_factory=FakeEmbedder,
        semantic=False, progress=progress))
    assert cli.main(['init', '--project', str(root), '--corpus', str(corpus)]) == 0
    assert cli.main(['index', '--project', str(root)]) == 0
    assert 'Index complete' in capsys.readouterr().err
    assert (root / '.strata' / 'ledger.db').exists()


def test_two_runtime_instances_serialize_refreshes(project, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import time
    from strata.corpus import sources
    root, _ = project
    apps = [runtime(root), runtime(root)]
    guard = threading.Lock(); active = 0; peak = 0
    original = sources.sync
    def observed(*args, **kwargs):
        nonlocal active, peak
        with guard:
            active += 1; peak = max(peak, active)
        try:
            time.sleep(0.05)
            return original(*args, **kwargs)
        finally:
            with guard:
                active -= 1
    monkeypatch.setattr(sources, 'sync', observed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda app: app.search(query='cutoff', kind='source'), apps))
    assert peak == 1
    assert results[0].hits == results[1].hits

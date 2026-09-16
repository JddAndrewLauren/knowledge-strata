"""Failures at trust boundaries, including retries and reopened databases."""
from dataclasses import replace
import pytest
from strata.corpus import sources
from strata.ledger import Ledger
from strata.index import Index, estimate_tokens, REPLY_TOKEN_BUDGET
from strata.embeddings import FakeEmbedder
from factories import make_note, make_source


def test_root_order_overlap_and_missing_root(tmp_path):
    a, b = tmp_path / 'a', tmp_path / 'b'
    a.mkdir(); b.mkdir(); (a / 'sub').mkdir()
    (a / 'sub' / 'entry.txt').write_text('Alpha evidence.')
    (b / 'entry.txt').write_text('Beta evidence.')
    ledger = Ledger(tmp_path / 'ledger.db')
    def sync(roots):
        return sources.sync(roots, ledger, cache_db=tmp_path / 'store.db')
    first = sync([a, b])
    revision = ledger.corpus_revision()
    assert sync([b, a, a / 'sub']).records == first.records
    assert ledger.corpus_revision() == revision
    with pytest.raises(OSError, match='unavailable'):
        sync([a, tmp_path / 'missing'])
    assert ledger.corpus_revision() == revision
    assert all(not deleted for _, deleted in ledger.known_units().values())
    assert sync([a, b]).records == first.records


def test_legacy_roots_require_mapping_and_preserve_anchors(tmp_path):
    root = tmp_path / 'corpus'; root.mkdir()
    (root / 'entry.txt').write_text('Original evidence.')
    ledger = Ledger(tmp_path / 'ledger.db')
    record = make_source(ledger, '000/entry.txt', ['Original evidence.'])
    with pytest.raises(ValueError, match='original ordered roots'):
        sources.sync([root], ledger, cache_db=tmp_path / 'store.db')
    result = sources.sync([root], ledger, cache_db=tmp_path / 'store.db', legacy_roots=[root])
    assert result.records[0].ref == record.ref
    assert ledger.text(record.ref + ' p1') == 'Original evidence.'


def test_scan_failure_rolls_back_alignment(tmp_path, monkeypatch):
    root = tmp_path / 'corpus'; root.mkdir()
    for name in ('a', 'b'):
        (root / f'{name}.txt').write_text(name + ' evidence.')
    ledger = Ledger(tmp_path / 'ledger.db')
    original = ledger.align
    def fail(path, *args, **kwargs):
        if path.endswith('b.txt'):
            raise OSError('interrupted')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(ledger, 'align', fail)
    with pytest.raises(OSError):
        sources.sync([root], ledger, cache_db=tmp_path / 'store.db')
    assert ledger.known_units() == {}
    ledger.close()
    reopened = Ledger(tmp_path / 'ledger.db')
    assert reopened.known_units() == {}
    assert len(sources.sync([root], reopened, cache_db=tmp_path / 'store.db').records) == 2


def test_failed_sync_retains_snapshot_and_retry_works(tmp_path):
    ledger = Ledger(tmp_path / 'ledger.db')
    def open_index():
        return Index(tmp_path / 'index.db', ledger=ledger, embedder=FakeEmbedder(), semantic=False)
    index = open_index()
    old = make_note('notes/project.md', ['Original evidence.'])
    index.sync([old]); revision = index.index_revision
    bad = replace(make_source(ledger, 'bad.txt', ['Expected.']), paragraphs=('Wrong.', 'Count.'))
    with pytest.raises(ValueError, match='align'):
        index.sync([bad])
    assert index.index_revision == revision
    assert index.read(old.ref).body == 'Original evidence.'
    assert index.indexing_state.startswith('incomplete')
    index.close(); index = open_index()
    assert index.read(old.ref).body == 'Original evidence.'
    index.sync([replace(old, paragraphs=('New evidence.',))])
    assert index.read(old.ref).body == 'New evidence.'
    assert index.indexing_state == 'complete'


def test_read_pages_oversized_metadata_without_changing_payload(tmp_path):
    ledger = Ledger(tmp_path / 'ledger.db')
    index = Index(tmp_path / 'index.db', ledger=ledger, embedder=FakeEmbedder(), semantic=False)
    note = make_note('notes/project.md', ['Evidence.'], title='long title ' * 6000,
                     warnings=('warning ' * 6000,))
    index.sync([note]); page = index.read(note.ref)
    metadata, body, seen = [], [], set()
    while True:
        assert estimate_tokens(page.text()) <= REPLY_TOKEN_BUDGET
        metadata.append(page.metadata or '')
        body.append(page.body)
        if not page.continuation:
            break
        assert page.continuation not in seen
        seen.add(page.continuation)
        page = index.read(cursor=page.continuation)
    assert ''.join(body) == 'Evidence.'
    assert note.title in ''.join(metadata)
    assert note.warnings[0] in ''.join(metadata)


def test_walk_permission_error_does_not_retire_sources(tmp_path, monkeypatch):
    root = tmp_path / 'corpus'; root.mkdir()
    (root / 'entry.txt').write_text('Evidence.')
    ledger = Ledger(tmp_path / 'ledger.db')
    sources.sync([root], ledger, cache_db=tmp_path / 'store.db')
    before = ledger.corpus_revision()
    def broken_walk(*args, onerror, **kwargs):
        onerror(PermissionError('archive access denied'))
        yield
    monkeypatch.setattr(sources.os, 'walk', broken_walk)
    with pytest.raises(PermissionError):
        sources.sync([root], ledger, cache_db=tmp_path / 'store.db')
    assert ledger.corpus_revision() == before
    assert all(not deleted for _, deleted in ledger.known_units().values())


def test_embedding_failure_rolls_back_fts_vectors_and_records(tmp_path, monkeypatch):
    ledger = Ledger(tmp_path / 'ledger.db'); embedder = FakeEmbedder()
    index = Index(tmp_path / 'index.db', ledger=ledger, embedder=embedder)
    original = make_note('notes/project.md', ['Original evidence.'])
    index.sync([original]); revision = index.index_revision
    real = embedder.embed_passages
    def fail(texts):
        raise RuntimeError('embedding interrupted')
    monkeypatch.setattr(embedder, 'embed_passages', fail)
    with pytest.raises(RuntimeError):
        index.sync([replace(original, paragraphs=('Replacement evidence.',))])
    assert index.index_revision == revision
    assert index.search('Original').lexical_records == 1
    assert index.search('Replacement').lexical_records == 0
    monkeypatch.setattr(embedder, 'embed_passages', real)
    index.sync([replace(original, paragraphs=('Replacement evidence.',))])
    assert index.search('Replacement').lexical_records == 1


def test_long_ref_read_cursor_remains_compact(tmp_path):
    ledger = Ledger(tmp_path / 'ledger.db')
    index = Index(tmp_path / 'index.db', ledger=ledger, embedder=FakeEmbedder(), semantic=False)
    note = make_note('notes/' + 'a/' * 20000 + 'item.md', ['Payload.'])
    index.sync([note]); page = index.read(note.ref); text = []
    while True:
        assert estimate_tokens(page.text()) <= REPLY_TOKEN_BUDGET
        text.append(page.body)
        if not page.continuation:
            break
        assert len(page.continuation) < 512
        page = index.read(cursor=page.continuation)
    assert ''.join(text) == 'Payload.'

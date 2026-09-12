"""Index changes mid-enumeration invalidate cursors explicitly; restarting
the original scope re-enumerates completely and deduplicated at the new
revision, rereads changed records, and never mixes text from two
revisions or reports a false completion (acceptance #22 test 4).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from strata.embeddings import FakeEmbedder
from strata.index import CursorError, Index
from strata.ledger import Ledger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import make_fixtures as fx  # noqa: E402

from factories import exact, make_note, make_source


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    yield led
    led.close()


def lexical_index(tmp_path, ledger):
    return Index(tmp_path / "index.db", ledger=ledger, embedder=FakeEmbedder(), semantic=False)


def _make_many(ledger, n=501):
    return [make_source(ledger, f"r{i:04d}.txt", [f"Text {i}"], date=exact("2001-06-01")) for i in range(n)]


def _drain(index, first):
    hits = list(first.hits)
    cursor = first.continuation
    while cursor:
        page = index.search(cursor=cursor)
        hits.extend(page.hits)
        cursor = page.continuation
    return hits


def test_a_source_change_mid_enumeration_invalidates_the_cursor(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    records = _make_many(ledger)
    index.sync(records)
    first = index.search(from_="2001-06-01", to="2001-06-01")
    assert first.continuation is not None

    changed = make_source(ledger, "r0000.txt", ["Changed text."], date=exact("2001-06-01"))
    index.sync(records[1:] + [changed])

    with pytest.raises(CursorError):
        index.search(cursor=first.continuation)
    index.close()


def test_an_added_source_mid_enumeration_invalidates_the_cursor(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    records = _make_many(ledger)
    index.sync(records)
    first = index.search(from_="2001-06-01", to="2001-06-01")
    added = make_source(ledger, "extra.txt", ["Extra."], date=exact("2001-06-01"))
    index.sync(records + [added])
    with pytest.raises(CursorError):
        index.search(cursor=first.continuation)
    index.close()


def test_a_deleted_source_mid_enumeration_invalidates_the_cursor(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    records = _make_many(ledger)
    index.sync(records)
    first = index.search(from_="2001-06-01", to="2001-06-01")
    index.sync(records[1:])
    with pytest.raises(CursorError):
        index.search(cursor=first.continuation)
    index.close()


def test_a_note_edit_mid_enumeration_invalidates_the_cursor(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    records = _make_many(ledger)
    note1 = make_note("notes/theme/x.md", ["First."], type="theme")
    index.sync(records + [note1])
    first = index.search(from_="2001-06-01", to="2001-06-01")
    note2 = make_note("notes/theme/x.md", ["Edited."], type="theme")
    index.sync(records + [note2])
    with pytest.raises(CursorError):
        index.search(cursor=first.continuation)
    index.close()


def test_a_dating_change_mid_enumeration_invalidates_the_cursor_and_moves_both_revisions(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    records = _make_many(ledger)
    index.sync(records)
    first = index.search(from_="2001-06-01", to="2001-06-30")
    corpus_before = first.corpus_revision

    redated = make_source(ledger, "r0000.txt", ["Text 0"], date=exact("2001-06-02"), dated=True)
    index.sync(records[1:] + [redated])
    with pytest.raises(CursorError):
        index.search(cursor=first.continuation)
    restarted = index.search(from_="2001-06-01", to="2001-06-30")
    assert restarted.index_revision != first.index_revision
    assert restarted.corpus_revision != corpus_before
    index.close()


def test_restarting_after_invalidation_enumerates_completely_deduplicated_and_rereads_the_changed_record(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    records = _make_many(ledger)
    index.sync(records)
    first = index.search(from_="2001-06-01", to="2001-06-01")
    seen_before = {h.ref for h in first.hits}
    assert records[0].ref in seen_before  # the record about to change was already shown
    assert first.continuation is not None  # the interrupted enumeration never claimed completion

    changed = make_source(ledger, "r0000.txt", ["Changed text, still on the same day."], date=exact("2001-06-01"))
    remaining = records[1:] + [changed]
    index.sync(remaining)
    with pytest.raises(CursorError):
        index.search(cursor=first.continuation)

    # restart the original scope (same arguments, no cursor) and deduplicate by ref
    restarted = index.search(from_="2001-06-01", to="2001-06-01")
    all_hits = _drain(index, restarted)
    refs = [h.ref for h in all_hits]
    assert len(refs) == len(set(refs)) == len(remaining)
    assert set(refs) == {r.ref for r in remaining}
    assert len(seen_before | set(refs)) == len(remaining)

    # the changed record is reread at the new revision, not carried over
    reread = next(h for h in all_hits if h.ref == changed.ref)
    assert reread.title == "Changed text, still on the same day."
    assert index.read(changed.ref).body == "Changed text, still on the same day."
    index.close()


def test_a_cache_rebuild_invalidates_a_pending_cursor(tmp_path, ledger):
    idx1 = lexical_index(tmp_path, ledger)
    records = _make_many(ledger)
    idx1.sync(records)
    first = idx1.search(from_="2001-06-01", to="2001-06-01")
    idx1.close()

    (tmp_path / "index.db").unlink()
    idx2 = lexical_index(tmp_path, ledger)
    idx2.sync(records)
    with pytest.raises(CursorError):
        idx2.search(cursor=first.continuation)
    idx2.close()


def test_a_read_cursor_is_invalidated_by_a_source_change(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    big = fx.oversized_paragraph(80_000)
    rec = make_source(ledger, "big.txt", [big], date=exact("2001-06-01"))
    index.sync([rec])
    first = index.read(rec.ref)
    assert first.continuation is not None

    other = make_source(ledger, "other.txt", ["Something else."], date=exact("2001-06-02"))
    index.sync([rec, other])

    with pytest.raises(CursorError):
        index.read(cursor=first.continuation)
    index.close()


def test_read_pages_from_two_revisions_are_never_concatenated(tmp_path, ledger):
    """A record changes between read pages: the pending page raises, the
    restarted read reproduces the new text exactly, and the stale first
    page is no prefix of it."""
    index = lexical_index(tmp_path, ledger)
    old_text = "OLD " + fx.oversized_paragraph(60_000)
    rec = make_source(ledger, "big.txt", [old_text], date=exact("2001-06-01"))
    index.sync([rec])
    stale_first = index.read(rec.ref)
    assert stale_first.continuation is not None

    new_text = "NEW " + fx.oversized_paragraph(60_000)
    index.sync([make_source(ledger, "big.txt", [new_text], date=exact("2001-06-01"))])
    with pytest.raises(CursorError):
        index.read(cursor=stale_first.continuation)

    reply = index.read(rec.ref)
    body = reply.body
    while reply.continuation:
        reply = index.read(cursor=reply.continuation)
        body += reply.body
    assert body == new_text
    assert not new_text.startswith(stale_first.body)
    index.close()


def test_an_assignment_cursor_is_invalidated_like_any_other(tmp_path, ledger):
    index = Index(tmp_path / "index.db", ledger=ledger, embedder=FakeEmbedder(), chunk_tokens=5, semantic=False)
    records = [make_source(ledger, f"r{i}.txt", [f"xxxxxxxx {i}"], date=exact("2001-06-01")) for i in range(4)]
    index.sync(records)
    first = index.search(from_="2001-06-01", to="2001-06-01")
    assert first.chunks
    index.sync(records[1:])
    with pytest.raises(CursorError):
        index.search(cursor=first.chunks[0].cursor)
    index.close()

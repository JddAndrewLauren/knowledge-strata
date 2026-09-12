"""Index changes mid-enumeration invalidate cursors explicitly; restarting
the original scope re-enumerates completely and deduplicated at the new
revision (acceptance #22 test 4).
"""

from __future__ import annotations

import pytest

from strata.embeddings import FakeEmbedder
from strata.index import CursorError, Index
from strata.ledger import Ledger

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


def test_restarting_after_invalidation_enumerates_completely_and_deduplicated(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    records = _make_many(ledger)
    index.sync(records)
    first = index.search(from_="2001-06-01", to="2001-06-01")

    changed = make_source(ledger, "r0000.txt", ["Changed text, still on the same day."], date=exact("2001-06-01"))
    remaining = records[1:] + [changed]
    index.sync(remaining)

    # restart the original scope (same arguments, no cursor)
    restarted = index.search(from_="2001-06-01", to="2001-06-01")
    all_hits = list(restarted.hits)
    cursor = restarted.continuation
    while cursor:
        page = index.search(cursor=cursor)
        all_hits.extend(page.hits)
        cursor = page.continuation

    refs = [h.ref for h in all_hits]
    assert len(refs) == len(remaining)
    assert len(set(refs)) == len(remaining)
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
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import make_fixtures as fx

    big = fx.oversized_paragraph(80_000)
    rec = make_source(ledger, "big.txt", [big], date=exact("2001-06-01"))
    index.sync([rec])
    first = index.read(rec.ref)
    assert first.continuation is not None

    other = make_source(ledger, "other.txt", ["Something else."], date=exact("2001-06-02"))
    index.sync([rec, other])

    from strata.index import CursorError

    with pytest.raises(CursorError):
        index.read(cursor=first.continuation)
    index.close()

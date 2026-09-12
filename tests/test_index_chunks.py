"""Reader assignments: chunk_tokens partitioning, day-over-budget split,
an oversized single record's segment count (#10 resolution ss4).
"""

from __future__ import annotations

import pytest

from strata.embeddings import FakeEmbedder
from strata.index import Index
from strata.ledger import Ledger

from factories import exact, make_source


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    yield led
    led.close()


def small_index(tmp_path, ledger, chunk_tokens=100):
    return Index(tmp_path / "index.db", ledger=ledger, embedder=FakeEmbedder(), chunk_tokens=chunk_tokens, semantic=False)


def _text_of_tokens(n_tokens: int) -> str:
    # estimate_tokens = ceil(utf8 bytes / 4); ascii, so 4 chars/token.
    return "x" * (n_tokens * 4)


def test_chunks_partition_records_under_the_budget_by_day(tmp_path, ledger):
    index = small_index(tmp_path, ledger, chunk_tokens=100)
    a = make_source(ledger, "a.txt", [_text_of_tokens(40)], date=exact("2001-06-01"))
    b = make_source(ledger, "b.txt", [_text_of_tokens(40)], date=exact("2001-06-02"))
    c = make_source(ledger, "c.txt", [_text_of_tokens(40)], date=exact("2001-06-03"))
    index.sync([a, b, c])

    reply = index.search(from_="2001-06-01", to="2001-06-30")
    total_records = sum(row.records for row in reply.chunks)
    assert total_records == 3
    for row in reply.chunks:
        assert row.tokens <= 100
    # a+b fit in one chunk (80<=100); c starts a new one.
    assert len(reply.chunks) == 2
    index.close()


def test_a_day_over_budget_splits_by_ref_order(tmp_path, ledger):
    index = small_index(tmp_path, ledger, chunk_tokens=50)
    a = make_source(ledger, "a.txt", [_text_of_tokens(30)], date=exact("2001-06-01"))
    b = make_source(ledger, "b.txt", [_text_of_tokens(30)], date=exact("2001-06-01"))
    index.sync([a, b])

    reply = index.search(from_="2001-06-01", to="2001-06-01")
    assert len(reply.chunks) == 2
    assert sum(row.records for row in reply.chunks) == 2
    index.close()


def test_an_oversized_single_record_gets_its_own_assignment_with_segments(tmp_path, ledger):
    index = small_index(tmp_path, ledger, chunk_tokens=50)
    big = make_source(ledger, "big.txt", [_text_of_tokens(180)], date=exact("2001-06-01"))
    index.sync([big])

    reply = index.search(from_="2001-06-01", to="2001-06-01")
    assert len(reply.chunks) == 1
    assert reply.chunks[0].records == 1
    assert reply.chunks[0].segments == 4  # ceil(180/50)
    index.close()


def test_no_record_is_assigned_twice(tmp_path, ledger):
    index = small_index(tmp_path, ledger, chunk_tokens=60)
    records = [
        make_source(ledger, f"r{i}.txt", [_text_of_tokens(20)], date=exact(f"2001-06-{i + 1:02d}"))
        for i in range(10)
    ]
    index.sync(records)
    reply = index.search(from_="2001-06-01", to="2001-06-10")
    seen_refs = set()
    for chunk in reply.chunks:
        # decode the assignment's scope from its cursor to check for duplicates
        import base64
        import json

        padded = chunk.cursor + "=" * (-len(chunk.cursor) % 4)
        state = json.loads(base64.urlsafe_b64decode(padded))
        for ref in state["scope"]:
            assert ref not in seen_refs
            seen_refs.add(ref)
    assert len(seen_refs) == 10
    index.close()


def test_chunks_carry_an_executable_search_cursor(tmp_path, ledger):
    index = small_index(tmp_path, ledger, chunk_tokens=100)
    a = make_source(ledger, "a.txt", [_text_of_tokens(40)], date=exact("2001-06-01"))
    index.sync([a])
    reply = index.search(from_="2001-06-01", to="2001-06-01")
    assert len(reply.chunks) == 1
    cursor = reply.chunks[0].cursor
    assert isinstance(cursor, str) and cursor
    index.close()

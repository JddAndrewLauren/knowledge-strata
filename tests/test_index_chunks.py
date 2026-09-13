"""Reader assignments: chunk_tokens partitioning, day-over-budget split,
an oversized record's bounded read segments, and executable assignment
cursors that run exactly their own slice (#10 resolution ss4; CONTEXT.md
"Chunk"; acceptance #22 test 2).
"""

from __future__ import annotations

import pytest

from strata.embeddings import FakeEmbedder
from strata.index import REPLY_TOKEN_BUDGET, Index, estimate_tokens
from strata.ledger import Ledger

from factories import exact, inferred_month, make_source, unknown


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


def _execute(index, cursor):
    """Run an assignment cursor to completion, the way a reader does:
    the cursor alone, every continuation followed. Returns the bare record
    refs it yielded, in order."""
    refs = []
    while cursor:
        reply = index.search(cursor=cursor)
        refs.extend(hit.ref.split(" ")[0] for hit in reply.hits)
        cursor = reply.continuation
    return refs


def _all_chunks(index, first):
    chunks = list(first.chunks)
    cursor = first.continuation
    while cursor:
        page = index.search(cursor=cursor)
        chunks.extend(page.chunks)
        cursor = page.continuation
    return chunks


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
    assert [_execute(index, row.cursor) for row in reply.chunks] == [[a.ref], [b.ref]]
    index.close()


def test_each_assignment_cursor_executes_exactly_its_own_records(tmp_path, ledger):
    """The reviewer's P1: three one-day records under a 50-token budget make
    three assignments; running one assignment's cursor yields that one
    record, not the whole day."""
    index = small_index(tmp_path, ledger, chunk_tokens=50)
    records = [make_source(ledger, f"{name}.txt", [_text_of_tokens(30)], date=exact("2001-06-01")) for name in "abc"]
    index.sync(records)

    reply = index.search(from_="2001-06-01", to="2001-06-01")
    assert len(reply.chunks) == 3
    executed = [_execute(index, row.cursor) for row in reply.chunks]
    assert executed == [[records[0].ref], [records[1].ref], [records[2].ref]]

    # The scoped reply describes its own slice: one record, its own row.
    scoped = index.search(cursor=reply.chunks[1].cursor)
    assert scoped.evidence_records == 1 and scoped.shown == 1
    assert scoped.tokens == 30
    assert [row.records for row in scoped.chunks] == [1]
    index.close()


def test_assignments_together_cover_the_evidence_once_including_unknown_and_partial_dates(tmp_path, ledger):
    index = small_index(tmp_path, ledger, chunk_tokens=60)
    dated = [
        make_source(ledger, f"r{i}.txt", [_text_of_tokens(20)], date=exact(f"2001-06-{i + 1:02d}")) for i in range(10)
    ]
    partial = make_source(ledger, "folder.txt", [_text_of_tokens(20)], date=inferred_month("2001-06-01", "folder"))
    undated = [make_source(ledger, f"u{i}.txt", [_text_of_tokens(20)], date=unknown()) for i in range(3)]
    index.sync(dated + [partial] + undated)

    first = index.search(from_="2001-06-01", to="2001-06-30")
    chunks = _all_chunks(index, first)
    assert sum(row.records for row in chunks) == 14
    executed = [_execute(index, row.cursor) for row in chunks]
    flat = [ref for refs in executed for ref in refs]
    assert len(flat) == len(set(flat)) == 14  # every record exactly once across the plan
    assert set(flat) == {r.ref for r in dated + [partial] + undated}
    # each executed slice matches its row's record count, and unknown comes last
    assert [len(refs) for refs in executed] == [row.records for row in chunks]
    assert chunks[-1].from_ == "unknown" and executed[-1] == sorted(u.ref for u in undated)
    index.close()


def test_a_large_assignment_cursor_stays_small_and_pages(tmp_path, ledger):
    """A busy-day assignment over many small records must not carry the
    ref list: the cursor stays compact, the reply stays bounded, and the
    slice pages under the hit cap."""
    index = small_index(tmp_path, ledger, chunk_tokens=80_000)
    records = [
        make_source(ledger, f"busy-{i:04d}.txt", [f"Busy note {i}."], date=exact("2001-05-17")) for i in range(1200)
    ]
    index.sync(records)
    first = index.search(from_="2001-05-17", to="2001-05-17")
    assert len(first.chunks) == 1
    assert first.chunks[0].records == 1200
    assert len(first.chunks[0].cursor) < 400
    assert estimate_tokens(first.text()) <= REPLY_TOKEN_BUDGET

    page = index.search(cursor=first.chunks[0].cursor)
    assert page.shown == 500 and page.continuation is not None
    assert estimate_tokens(page.text()) <= REPLY_TOKEN_BUDGET
    assert _execute(index, first.chunks[0].cursor) == sorted(r.ref for r in records)
    index.close()


def test_an_oversized_record_spans_bounded_read_segments(tmp_path, ledger):
    """A record over the budget becomes one assignment per bounded read
    segment, each marked ``k of n``; no single segment's reply describes
    the record as complete."""
    index = small_index(tmp_path, ledger, chunk_tokens=50)
    big = make_source(ledger, "big.txt", [_text_of_tokens(180)], date=exact("2001-06-01"))
    index.sync([big])

    reply = index.search(from_="2001-06-01", to="2001-06-01")
    assert [row.segment for row in reply.chunks] == [(1, 4), (2, 4), (3, 4), (4, 4)]  # ceil(180/50)
    assert [row.tokens for row in reply.chunks] == [50, 50, 50, 30]
    assert all(row.records == 1 for row in reply.chunks)
    assert "segment 2 of 4" in reply.chunks[1].line()

    for k, row in enumerate(reply.chunks, start=1):
        assert _execute(index, row.cursor) == [big.ref]
        scoped = index.search(cursor=row.cursor)
        assert [r.segment for r in scoped.chunks] == [(k, 4)]
        assert scoped.chunks[0].tokens == row.tokens
    index.close()


def test_a_scoped_cursor_keeps_the_query_and_its_filters(tmp_path, ledger):
    index = small_index(tmp_path, ledger, chunk_tokens=30)
    hit_a = make_source(ledger, "a.txt", ["The cutoff held. " + _text_of_tokens(20)], date=exact("2001-06-01"))
    hit_b = make_source(ledger, "b.txt", ["The cutoff held. " + _text_of_tokens(20)], date=exact("2001-06-02"))
    miss = make_source(ledger, "c.txt", ["Nothing here. " + _text_of_tokens(20)], date=exact("2001-06-01"))
    index.sync([hit_a, hit_b, miss])

    reply = index.search("cutoff", from_="2001-06-01", to="2001-06-30")
    assert len(reply.chunks) == 2
    executed = [_execute(index, row.cursor) for row in reply.chunks]
    assert executed == [[hit_a.ref], [hit_b.ref]]
    scoped = index.search(cursor=reply.chunks[0].cursor)
    assert scoped.lexical_records == 1 and scoped.hits[0].ref == f"{hit_a.ref} p1"
    index.close()

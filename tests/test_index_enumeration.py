"""Enumeration beyond limits (acceptance #22 test 1): browse a day with more
than 500 records, a query with more than 100 records and more than 200
lexical paragraphs, semantic-only evidence, more than 500 undated records,
and overlapping inferred dates - all followed to completion with no missing
or duplicate records, stable ordering, and preserved filters. Conflicting
cursor arguments are rejected, and a covered source stays enumerable.
"""

from __future__ import annotations

import pytest

from strata.embeddings import FakeEmbedder
from strata.index import CursorError, Index, estimate_tokens
from strata.ledger import Ledger

from factories import exact, inferred_month, inferred_year, make_source, unknown


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    yield led
    led.close()


def lexical_index(tmp_path, ledger):
    return Index(tmp_path / "index.db", ledger=ledger, embedder=FakeEmbedder(), semantic=False)


def _follow_hits(index, first_reply, **kwargs):
    hits = list(first_reply.hits)
    cursor = first_reply.continuation
    seen_cursors = {cursor} if cursor else set()
    while cursor is not None:
        reply = index.search(cursor=cursor)
        hits.extend(reply.hits)
        cursor = reply.continuation
        if cursor is not None:
            assert cursor not in seen_cursors, "cursor loop: the same continuation was issued twice"
            seen_cursors.add(cursor)
    return hits


def test_busy_day_over_500_records_enumerates_completely(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    records = [
        make_source(ledger, f"busy-{i:04d}.txt", [f"Cutoff note number {i}: the eleven o'clock cutoff held."], date=exact("2001-05-17"))
        for i in range(520)
    ]
    index.sync(records)

    first = index.search(from_="2001-05-17", to="2001-05-17")
    hits = _follow_hits(index, first)
    refs = [h.ref for h in hits]
    assert len(refs) == 520
    assert len(set(refs)) == 520  # no duplicates
    assert refs == sorted(refs)  # browse order is stable (iso ties broken by ref)
    index.close()


def test_query_over_100_records_and_200_lexical_paragraphs_enumerates_completely(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    records = [
        make_source(
            ledger,
            f"r{i:04d}.txt",
            [f"The cutoff moved to eleven, item {i}.", f"A second cutoff mention, item {i}."],
            date=exact(f"2001-06-{(i % 28) + 1:02d}"),
        )
        for i in range(150)
    ]
    index.sync(records)

    first = index.search("cutoff")
    assert first.lexical_records == 150
    assert first.lexical_paragraphs == 300
    hits = _follow_hits(index, first)
    refs = [h.ref for h in hits]
    assert len(set(r.split(" ")[0] for r in refs)) == 150  # every record shown once
    assert len(refs) == 150
    index.close()


def test_scale_semantic_only_top_200_cutoff(tmp_path, ledger):
    matched_texts = [f"The interconnection tripped, incident {i}." for i in range(12)]
    embedder = FakeEmbedder(topics={"outage": "tie-outage"})
    for text in matched_texts:
        embedder.place(text, "tie-outage")
    index = Index(tmp_path / "index.db", ledger=ledger, embedder=embedder)

    matched = [make_source(ledger, f"m{i}.txt", [matched_texts[i]], date=exact("2001-06-19")) for i in range(12)]
    background = [
        make_source(ledger, f"bg{i:04d}.txt", [f"Background filler paragraph {i}."], date=exact("2001-06-20"))
        for i in range(250)
    ]
    index.sync(matched + background)

    reply = index.search("outage")
    assert reply.lexical_records == 0
    matched_refs = {r.ref for r in matched}
    hit_refs = {h.ref.split(" ")[0] for h in reply.hits[: len(matched)]}
    # every truly-matched (similarity 1.0) record beats every background
    # (near-zero) one, regardless of how the top-200 cutoff falls among
    # background records.
    assert matched_refs <= hit_refs
    assert reply.evidence_records == 200  # the semantic branch's hard cap
    index.close()


def test_scale_undated_over_500_enumerates_completely(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    records = [make_source(ledger, f"u{i:04d}.txt", [f"Undated desk note {i}."], date=unknown()) for i in range(520)]
    index.sync(records)

    first = index.search()
    hits = _follow_hits(index, first)
    assert len(hits) == 520
    assert len(set(h.ref for h in hits)) == 520
    assert first.undated == 520 or sum(1 for h in hits if h.date.confidence == "unknown") == 520
    index.close()


def test_overlapping_inferred_dates_enumerate_without_duplicates(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    records = [
        make_source(ledger, "month.txt", ["Month record."], date=inferred_month("2001-06-01", "folder")),
        make_source(ledger, "year.txt", ["Year record."], date=inferred_year("2001-01-01", "folder")),
        make_source(ledger, "day.txt", ["Day record."], date=exact("2001-06-15")),
    ]
    index.sync(records)
    first = index.search(from_="2001-06-01", to="2001-06-30")
    hits = _follow_hits(index, first)
    assert len(hits) == 3
    assert len(set(h.ref for h in hits)) == 3
    index.close()


def test_conflicting_cursor_arguments_are_rejected(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    records = [make_source(ledger, f"r{i}.txt", [f"Text {i}"], date=exact("2001-06-01")) for i in range(3)]
    index.sync(records)
    # A cursor must resume its own scope alone; any other argument alongside
    # it is rejected before the cursor's own validity is even considered.
    with pytest.raises(CursorError):
        index.search(query="something else", cursor="anything")
    with pytest.raises(CursorError):
        index.search(kind="note", cursor="anything")
    index.close()


def test_an_invalidated_cursor_names_the_original_scope(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    records = [make_source(ledger, f"r{i}.txt", [f"Text {i}"], date=exact("2001-06-01")) for i in range(3)]
    index.sync(records)
    reply = index.search(from_="2001-06-01", to="2001-06-30")
    from strata.index import _encode_cursor

    stale_cursor = _encode_cursor(
        {
            "t": "search",
            "rev": "stale-revision",
            "query": "",
            "from": "2001-06-01",
            "to": "2001-06-30",
            "who": None,
            "kind": None,
            "off": {"hits": 0, "month": 0, "covered": 0, "chunks": 0},
        }
    )
    with pytest.raises(CursorError, match="2001-06-01"):
        index.search(cursor=stale_cursor)
    index.close()


def test_honest_hybrid_budgets_equal_an_independent_recomputation(tmp_path, ledger):
    """Acceptance #22 test 2: compute the expected all-lexical-plus-
    semantic-only union independently of the index, and assert tokens,
    evidence counts and by_month equal it exactly."""
    embedder = FakeEmbedder(topics={"cutoff": "cutoff-topic"})
    index = Index(tmp_path / "index.db", ledger=ledger, embedder=embedder)

    lexical_records = [
        make_source(
            ledger,
            f"lex{i:04d}.txt",
            [f"The cutoff moved to eleven, item {i}.", f"A second cutoff mention, item {i}."],
            date=exact(f"2001-06-{(i % 28) + 1:02d}"),
        )
        for i in range(150)
    ]
    semantic_only_texts = [f"Related without the word, incident {i}." for i in range(8)]
    for text in semantic_only_texts:
        embedder.place(text, "cutoff-topic")
    semantic_only_records = [
        make_source(ledger, f"sem{i:04d}.txt", [semantic_only_texts[i]], date=exact("2001-07-01"))
        for i in range(8)
    ]
    index.sync(lexical_records + semantic_only_records)

    reply = index.search("cutoff")

    # Independent recomputation, not sharing code with the index's own SQL.
    expected_records = {r.ref: r for r in lexical_records + semantic_only_records}
    expected_tokens = sum(estimate_tokens("\n\n".join(r.paragraphs)) for r in expected_records.values())
    expected_by_month: dict[str, list[int]] = {}
    for r in expected_records.values():
        expected_by_month.setdefault(r.date.iso[:7], []).append(estimate_tokens("\n\n".join(r.paragraphs)))

    assert reply.lexical_records == 150
    assert reply.lexical_paragraphs == 300
    assert reply.evidence_records == len(expected_records) == 158
    assert reply.tokens == expected_tokens
    got_by_month = {row.month: (row.records, row.tokens) for row in reply.by_month}
    for month, toks in expected_by_month.items():
        assert got_by_month[month] == (len(toks), sum(toks))
    index.close()


def test_covered_sources_remain_enumerable(tmp_path, ledger):
    from factories import make_note

    index = lexical_index(tmp_path, ledger)
    src = make_source(ledger, "a.txt", ["Content about the meeting."], date=exact("2001-06-01"))
    index.sync([src])
    d = make_note(
        "notes/digest/2001-06-01--2001-06-30.md",
        ["Digest."],
        type="digest",
        window=("2001-06-01", "2001-06-30"),
        corpus_revision=str(ledger.corpus_revision()),
        coverage_complete=True,
    )
    index.sync([src, d])
    reply = index.search(from_="2001-06-01", to="2001-06-30", kind="source")
    assert reply.covered
    assert src.ref in {h.ref.split(" ")[0] for h in reply.hits}
    index.close()

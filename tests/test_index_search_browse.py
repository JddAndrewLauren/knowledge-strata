"""search(): browse mode (empty query), date-as-hint filtering, header
format (#10 ss9, ss6; CONTEXT.md "Date"/"Partial date").
"""

from __future__ import annotations

import pytest

from strata.embeddings import FakeEmbedder
from strata.index import Index
from strata.ledger import Ledger

from factories import exact, inferred_month, inferred_year, make_source, unknown


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    yield led
    led.close()


@pytest.fixture
def index(tmp_path, ledger):
    idx = Index(tmp_path / "index.db", ledger=ledger, embedder=FakeEmbedder())
    yield idx
    idx.close()


def test_browse_orders_by_iso_then_ref_unknown_last(index):
    ledger = index._ledger
    a = make_source(ledger, "a.txt", ["A"], date=exact("2001-06-02"))
    b = make_source(ledger, "b.txt", ["B"], date=exact("2001-06-01"))
    c = make_source(ledger, "c.txt", ["C"], date=unknown())
    d = make_source(ledger, "d.txt", ["D"], date=exact("2001-06-01"))
    index.sync([a, b, c, d])

    reply = index.search()
    order = [hit.ref for hit in reply.hits]
    # b and d tie on iso; ref breaks the tie. c (unknown) is last.
    assert order == [b.ref, d.ref, a.ref, c.ref]


def test_browse_lexical_total_is_always_zero(index):
    ledger = index._ledger
    a = make_source(ledger, "a.txt", ["Hello."], date=exact("2001-06-01"))
    index.sync([a])
    reply = index.search()
    assert (reply.lexical_records, reply.lexical_paragraphs) == (0, 0)
    assert reply.evidence_records == 1


def test_a_date_range_keeps_unknown_and_drops_only_out_of_range_records(index):
    ledger = index._ledger
    inside = make_source(ledger, "in.txt", ["In."], date=exact("2001-06-15"))
    outside = make_source(ledger, "out.txt", ["Out."], date=exact("2001-07-01"))
    undated = make_source(ledger, "u.txt", ["U."], date=unknown())
    index.sync([inside, outside, undated])

    reply = index.search(from_="2001-06-01", to="2001-06-30")
    refs = {hit.ref for hit in reply.hits}
    assert refs == {inside.ref, undated.ref}


def test_partial_dates_pass_range_by_period_overlap(index):
    ledger = index._ledger
    month_rec = make_source(ledger, "m.txt", ["M."], date=inferred_month("2001-06-01", "folder"))
    year_rec = make_source(ledger, "y.txt", ["Y."], date=inferred_year("2001-01-01", "folder"))
    index.sync([month_rec, year_rec])

    reply = index.search(from_="2001-06-01", to="2001-06-30")
    refs = {hit.ref for hit in reply.hits}
    # The month record's whole period (June) overlaps; the year record's
    # period (all of 2001) also overlaps the June window.
    assert refs == {month_rec.ref, year_rec.ref}

    reply2 = index.search(from_="2002-01-01", to="2002-12-31")
    assert reply2.hits == []


def test_hit_dates_show_granularity_and_origin_never_a_rounded_day(index):
    ledger = index._ledger
    month_rec = make_source(ledger, "m.txt", ["M."], date=inferred_month("2001-05-01", "folder"))
    undated = make_source(ledger, "u.txt", ["U."], date=unknown())
    index.sync([month_rec, undated])

    reply = index.search()
    by_ref = {hit.ref: hit for hit in reply.hits}
    from strata.index import display_date

    assert display_date(by_ref[month_rec.ref].date) == "2001-05 (folder)"
    assert display_date(by_ref[undated.ref].date) == "undated"


def test_kind_filters_to_one_literal(index):
    ledger = index._ledger
    src = make_source(ledger, "a.txt", ["A."], date=exact("2001-06-01"))
    from factories import make_note

    note = make_note("notes/theme/x.md", ["N."], type="theme")
    index.sync([src, note])

    reply = index.search(kind="note")
    assert [hit.ref for hit in reply.hits] == [note.ref]


def test_header_text_matches_the_documented_field_order(index):
    ledger = index._ledger
    a = make_source(ledger, "a.txt", ["A."], date=exact("2001-06-01"))
    index.sync([a])
    reply = index.search()
    text = reply.text()
    labels = [line.split()[0] for line in text.splitlines() if line and not line.startswith(" ")]
    assert labels[:13] == [
        "indexing",
        "index_revision",
        "corpus_revision",
        "lexical_total",
        "evidence_total",
        "tokens",
        "by_month",
        "undated",
        "inferred",
        "covered",
        "chunks",
        "shown",
        "continuations",
    ]
    assert "reply_tokens" in text

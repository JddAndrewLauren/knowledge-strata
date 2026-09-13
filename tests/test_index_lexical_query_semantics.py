"""Lexical query semantics (#46): terms combined with AND, double quotes
mark a phrase, ``who`` stays exact-phrase regardless. Lexical-only index
(``semantic=False``), same rationale as test_index_search_query.py: the
always-on semantic branch would confound assertions about lexical matching
specifically.
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


def lexical_index(tmp_path, ledger):
    return Index(tmp_path / "index.db", ledger=ledger, embedder=FakeEmbedder(), semantic=False)


def test_and_terms_match_regardless_of_order_or_adjacency(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    reversed_order = make_source(ledger, "a.txt", ["The desk kept trading gas all winter."], date=exact("2001-06-01"))
    non_adjacent = make_source(
        ledger, "b.txt", ["The desk moved gas and power trading to Houston."], date=exact("2001-06-02")
    )
    miss = make_source(ledger, "c.txt", ["Nothing relevant here."], date=exact("2001-06-03"))
    index.sync([reversed_order, non_adjacent, miss])

    reply = index.search("gas trading")
    refs = {h.ref.split(" ")[0] for h in reply.hits}
    assert refs == {reversed_order.ref, non_adjacent.ref}
    assert reply.lexical_paragraphs == 2
    index.close()


def test_quoted_query_stays_an_exact_phrase(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    reversed_order = make_source(ledger, "a.txt", ["The desk kept trading gas all winter."], date=exact("2001-06-01"))
    non_adjacent = make_source(
        ledger, "b.txt", ["The desk moved gas and power trading to Houston."], date=exact("2001-06-02")
    )
    phrase_hit = make_source(ledger, "c.txt", ["The gas trading desk closed early."], date=exact("2001-06-03"))
    index.sync([reversed_order, non_adjacent, phrase_hit])

    reply = index.search('"gas trading"')
    assert {h.ref.split(" ")[0] for h in reply.hits} == {phrase_hit.ref}
    index.close()


@pytest.mark.parametrize(
    "term,text",
    [
        ("e-mail", "Please send the e-mail by noon."),
        ("2001-06", "Filed under 2001-06 in the archive."),
        ("Priya:", "Priya: please review the attached schedule."),
        ("gas*", "The gas* placeholder appeared in the template."),
        ("NOT", "The memo said NOT to proceed without sign-off."),
    ],
)
def test_fts5_syntax_characters_are_matched_literally(tmp_path, ledger, term, text):
    index = lexical_index(tmp_path, ledger)
    hit = make_source(ledger, "hit.txt", [text], date=exact("2001-06-01"))
    index.sync([hit])

    reply = index.search(term)
    assert [h.ref.split(" ")[0] for h in reply.hits] == [hit.ref]
    index.close()


def test_bare_open_paren_raises_nothing(tmp_path, ledger):
    """A syntax character with no surrounding token (unicode61 discards
    stray punctuation) matches nothing rather than raising - still the
    literal-quoting contract at work, just an empty phrase."""
    index = lexical_index(tmp_path, ledger)
    hit = make_source(ledger, "hit.txt", ["Costs are shown in parens ( unaudited )."], date=exact("2001-06-01"))
    index.sync([hit])

    reply = index.search("(")
    assert reply.hits == []
    index.close()


def test_unbalanced_quote_runs_to_the_end_of_the_query_as_one_phrase(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    phrase_hit = make_source(ledger, "a.txt", ["The gas trading desk closed early."], date=exact("2001-06-01"))
    non_adjacent = make_source(
        ledger, "b.txt", ["The desk moved gas and power trading to Houston."], date=exact("2001-06-02")
    )
    index.sync([phrase_hit, non_adjacent])

    reply = index.search('"gas trading')
    assert {h.ref.split(" ")[0] for h in reply.hits} == {phrase_hit.ref}
    index.close()


@pytest.mark.parametrize("query", ["   ", '""', '"   "', '  ""  '])
def test_whitespace_or_quotes_only_query_behaves_as_the_empty_browse_query(tmp_path, ledger, query):
    index = lexical_index(tmp_path, ledger)
    rec = make_source(ledger, "a.txt", ["Text."], date=exact("2001-06-01"))
    index.sync([rec])

    empty = index.search()
    reply = index.search(query)
    assert [h.ref for h in reply.hits] == [h.ref for h in empty.hits]
    assert reply.lexical_records == 0
    assert "\n" not in reply.hits[0].line()  # browse hits carry no snippet
    index.close()


def test_snippet_highlights_a_lexical_hit_regardless_of_term_order(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    rec = make_source(
        ledger,
        "a.txt",
        ["Unrelated opener paragraph.", "The desk kept trading gas all winter long."],
        date=exact("2001-06-01"),
    )
    index.sync([rec])

    reply = index.search("gas trading")
    hit = reply.hits[0]
    assert hit.snippet
    assert "trading" in hit.snippet and "gas" in hit.snippet
    index.close()

"""search(): hybrid query evidence, RRF ordering, one hit per record at its
best paragraph, and the hit-line format (#10 resolution ss1, ss6).

Purely-lexical scenarios open the index with ``semantic=False`` (a
lexical-only degrade, same path as no vec0 extension) so the always-on
top-200 semantic branch - which legitimately pulls in the *whole* scope
whenever it holds 200 paragraphs or fewer (design.md: no relevance
threshold) - does not confound assertions that are about lexical matching
specifically. Hybrid behaviour gets its own dedicated tests below.
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


def hybrid_index(tmp_path, ledger, embedder=None):
    return Index(tmp_path / "index.db", ledger=ledger, embedder=embedder or FakeEmbedder())


def test_lexical_query_matches_only_records_carrying_the_phrase(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    hit = make_source(ledger, "hit.txt", ["The cutoff moved to eleven."], date=exact("2001-06-01"))
    miss = make_source(ledger, "miss.txt", ["Nothing relevant here."], date=exact("2001-06-02"))
    index.sync([hit, miss])

    reply = index.search("cutoff")
    assert [h.ref for h in reply.hits] == ["SRC-000001 p1"]
    assert reply.lexical_records == 1
    assert reply.lexical_paragraphs == 1
    assert reply.evidence_records == 1
    index.close()


def test_one_hit_per_record_at_its_best_paragraph_with_match_count(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    rec = make_source(
        ledger,
        "multi.txt",
        ["No match here.", "The cutoff moved to eleven.", "Also cutoff mentioned again."],
        date=exact("2001-06-14"),
    )
    index.sync([rec])

    reply = index.search("cutoff")
    assert len(reply.hits) == 1
    hit = reply.hits[0]
    assert hit.matches == 2
    assert hit.ref in ("SRC-000001 p2", "SRC-000001 p3")  # whichever bm25 ranks best
    assert "(2 matches)" in hit.line()
    index.close()


def test_semantic_only_evidence_when_lexical_count_is_zero(tmp_path, ledger):
    embedder = FakeEmbedder(topics={"outage": "tie-outage"})
    embedder.place("The interconnection tripped and service stopped.", "tie-outage")
    index = hybrid_index(tmp_path, ledger, embedder)
    semantic_hit = make_source(
        ledger, "sem.txt", ["The interconnection tripped and service stopped."], date=exact("2001-06-19")
    )
    index.sync([semantic_hit])

    reply = index.search("outage")
    assert reply.lexical_records == 0
    assert reply.lexical_paragraphs == 0
    assert reply.evidence_records == 1
    assert [h.ref for h in reply.hits] == ["SRC-000001 p1"]
    index.close()


def test_hit_line_format_matches_the_documented_shape(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    rec = make_source(
        ledger,
        "westside.txt",
        [
            "Unrelated opener paragraph.",
            "The desk moved the schedule to Friday because the Portland office asked for it in writing.",
        ],
        date=exact("2001-06-14"),
        title="Re: Westside schedule for Friday",
    )
    index.sync([rec])
    reply = index.search("Portland office")
    hit = reply.hits[0]
    line = hit.line()
    assert line.startswith("SRC-000001 p2  2001-06-14  source  Re: Westside schedule for Friday")
    assert "\n    " in line  # snippet on its own indented line
    index.close()


def test_browse_hits_have_no_snippet_line(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    rec = make_source(ledger, "a.txt", ["Text."], date=exact("2001-06-01"))
    index.sync([rec])
    reply = index.search()
    assert "\n" not in reply.hits[0].line()
    index.close()


def test_rrf_favors_a_record_matching_both_branches(tmp_path, ledger):
    embedder = FakeEmbedder(topics={"cutoff": "cutoff-topic", "The eleven o'clock cutoff held today.": "cutoff-topic"})
    index = hybrid_index(tmp_path, ledger, embedder)
    both = make_source(ledger, "both.txt", ["The eleven o'clock cutoff held today."], date=exact("2001-06-01"))
    lexical_only = make_source(ledger, "lex.txt", ["A cutoff was mentioned once in passing."], date=exact("2001-06-02"))
    index.sync([both, lexical_only])

    reply = index.search("cutoff")
    assert reply.hits[0].ref == f"{both.ref} p1"
    index.close()

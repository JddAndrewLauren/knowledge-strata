"""Bounded replies and exact reconstruction (acceptance #22 test 3): long
titles, long month/coverage/assignment lists, and oversized source/note/
manuscript text all stay within the ~8,000-token estimate per serialized
reply, every list still advances, and concatenating read pages reproduces
the exact stored text (multibyte, combining, tab, CRLF, repeated
whitespace).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from strata.embeddings import FakeEmbedder
from strata.index import REPLY_TOKEN_BUDGET, Index, estimate_tokens
from strata.ledger import Ledger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import make_fixtures as fx  # noqa: E402

from factories import exact, make_manuscript, make_note, make_source


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    yield led
    led.close()


def lexical_index(tmp_path, ledger, **kwargs):
    return Index(tmp_path / "index.db", ledger=ledger, embedder=FakeEmbedder(), semantic=False, **kwargs)


def _drain(index, first, **search_kwargs):
    hits = list(first.hits)
    seen = set()
    cursor = first.continuation
    while cursor:
        assert cursor not in seen
        seen.add(cursor)
        reply = index.search(cursor=cursor)
        hits.extend(reply.hits)
        cursor = reply.continuation
    return hits


def test_long_titles_keep_the_reply_bounded_and_still_enumerate(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    long_title = "T" * 3000
    records = [
        make_source(ledger, f"r{i:03d}.txt", [f"Text {i}."], date=exact("2001-06-01"), title=long_title)
        for i in range(20)
    ]
    index.sync(records)
    first = index.search()
    assert estimate_tokens(first.text()) <= REPLY_TOKEN_BUDGET
    hits = _drain(index, first)
    assert len(set(h.ref for h in hits)) == 20


def test_a_long_month_list_pages_within_budget(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    records = [
        make_source(ledger, f"m{i:03d}.txt", [f"Content {i}."], date=exact(f"20{i // 12 % 100:02d}-{i % 12 + 1:02d}-01"))
        for i in range(300)
    ]
    index.sync(records)
    first = index.search()
    assert estimate_tokens(first.text()) <= REPLY_TOKEN_BUDGET
    months = list(first.by_month)
    cursor = first.continuation
    seen = set()
    while cursor:
        assert cursor not in seen
        seen.add(cursor)
        reply = index.search(cursor=cursor)
        assert estimate_tokens(reply.text()) <= REPLY_TOKEN_BUDGET
        months.extend(reply.by_month)
        cursor = reply.continuation
    assert len(months) == len({m.month for m in months}) == 300


def test_a_long_chunk_assignment_list_pages_within_budget(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger, chunk_tokens=5)
    records = [make_source(ledger, f"c{i:03d}.txt", [f"xxxxxxxx {i}"], date=exact(f"2001-06-{(i % 28) + 1:02d}")) for i in range(120)]
    index.sync(records)
    first = index.search(from_="2001-06-01", to="2001-06-28")
    assert estimate_tokens(first.text()) <= REPLY_TOKEN_BUDGET
    chunks = list(first.chunks)
    cursor = first.continuation
    seen = set()
    while cursor:
        assert cursor not in seen
        seen.add(cursor)
        reply = index.search(cursor=cursor)
        assert estimate_tokens(reply.text()) <= REPLY_TOKEN_BUDGET
        chunks.extend(reply.chunks)
        cursor = reply.continuation
    total_assigned = sum(row.records for row in chunks)
    assert total_assigned == 120


def test_oversized_source_paragraph_reads_page_and_reconstruct_exactly(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    big = fx.oversized_paragraph(40_000)
    rec = make_source(ledger, "big.txt", ["Intro.", big, "Outro."], date=exact("2001-06-01"))
    index.sync([rec])
    reply = index.read(rec.ref)
    body = reply.body
    cursor = reply.continuation
    while cursor:
        assert estimate_tokens(reply.text()) <= REPLY_TOKEN_BUDGET
        reply = index.read(cursor=cursor)
        body += reply.body
        cursor = reply.continuation
    assert big in body


def test_oversized_note_reads_page_and_reconstruct_exactly(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    big = fx.oversized_paragraph(40_000)
    note = make_note("notes/theme/big.md", ["Intro.", big], type="theme")
    index.sync([note])
    reply = index.read(note.ref)
    body = reply.body
    cursor = reply.continuation
    while cursor:
        reply = index.read(cursor=cursor)
        body += reply.body
        cursor = reply.continuation
    assert big in body


def test_oversized_manuscript_section_reads_page_and_reconstruct_exactly(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    big = fx.oversized_paragraph(40_000)
    manuscript = make_manuscript("manuscript/ch99.md", ["# July 2001", "## The long week", big, "## After", "Short."])
    index.sync([manuscript])
    reply = index.read("manuscript/ch99.md # The long week")
    body = reply.body
    cursor = reply.continuation
    while cursor:
        reply = index.read(cursor=cursor)
        body += reply.body
        cursor = reply.continuation
    assert big in body
    assert "Short." not in body


def test_retired_text_reply_stays_bounded(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    v1 = make_source(ledger, "a.txt", ["Original wording, not too long."], date=exact("2001-06-01"))
    index.sync([v1])
    v2 = make_source(ledger, "a.txt", ["Changed wording, still short."], date=exact("2001-06-01"))
    index.sync([v2])
    reply = index.read(f"{v1.ref} p1")
    assert estimate_tokens(reply.text()) <= REPLY_TOKEN_BUDGET
    assert "Original wording, not too long." in reply.body


def test_a_long_covered_list_pages_within_budget(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger, chunk_tokens=80_000)
    src = make_source(ledger, "a.txt", ["Content."], date=exact("2001-06-01"))
    index.sync([src])
    revision = str(ledger.corpus_revision())
    # Many distinct digest notes whose windows all overlap the query range,
    # to build a long covered list.
    many_digests = []
    for i in range(60):
        many_digests.append(
            make_note(
                f"notes/digest/2001-01-01--2001-12-31-{i}.md",
                ["Digest."],
                type="digest",
                window=("2001-01-01", "2001-12-31"),
                corpus_revision=revision,
                coverage_complete=True,
            )
        )
    index.sync([src, *many_digests])
    reply = index.search(from_="2001-01-01", to="2001-12-31")
    assert estimate_tokens(reply.text()) <= REPLY_TOKEN_BUDGET
    covered = list(reply.covered)
    cursor = reply.continuation
    seen = set()
    while cursor:
        assert cursor not in seen
        seen.add(cursor)
        page = index.search(cursor=cursor)
        covered.extend(page.covered)
        cursor = page.continuation
    assert len(covered) == 60

"""Bounded replies and exact reconstruction (acceptance #22 test 3): long
titles, long month/coverage/assignment lists, and oversized source/note/
manuscript text all stay within the ~8,000-token estimate per serialized
reply, every list still advances, and concatenating read payloads
reproduces the exact stored text (multibyte, combining, tab, CRLF,
repeated and trailing whitespace) with no transport label inside it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from strata.embeddings import FakeEmbedder
from strata.index import PARAGRAPH_SEPARATOR, REPLY_TOKEN_BUDGET, Index, estimate_tokens
from strata.ledger import Ledger
from strata.record import cap_at_word_boundary

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import make_fixtures as fx  # noqa: E402

from factories import exact, make_manuscript, make_note, make_source


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    yield led
    led.close()


@pytest.fixture(autouse=True)
def _every_search_reply_stays_within_budget(monkeypatch):
    """Acceptance (issue #42): the fit loop has no branch that sends a
    candidate reply that failed its own budget check - checked here for
    every reply this file produces, with no tolerance, rather than only the
    ones each test happens to assert on."""
    original = Index.search

    def guarded(self, *args, **kwargs):
        reply = original(self, *args, **kwargs)
        assert estimate_tokens(reply.text()) <= REPLY_TOKEN_BUDGET
        return reply

    monkeypatch.setattr(Index, "search", guarded)


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


def _read_pages(index, ref):
    """Every page of a read, each asserted within budget; returns the pages
    and the concatenated payload."""
    reply = index.read(ref)
    pages = [reply]
    while reply.continuation:
        assert estimate_tokens(reply.text()) <= REPLY_TOKEN_BUDGET
        reply = index.read(cursor=reply.continuation)
        pages.append(reply)
    assert estimate_tokens(reply.text()) <= REPLY_TOKEN_BUDGET
    return pages, "".join(page.body for page in pages)


def _awkward_text(chars: int = 40_000) -> str:
    """The fixture's multibyte/combining/tab/repeated-whitespace paragraph
    with CRLF line ends, trailing whitespace and an empty line added, so
    every character class the checkbox names is inside one paragraph."""
    big = fx.oversized_paragraph(chars)
    return big.replace("; ", ";\r\n").replace("wait.", "wait.  \r\n\r\n") + "  \t\r\n"


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


def test_a_60000_char_title_hit_stays_bounded_and_capped_on_a_word_boundary(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    long_title = " ".join(f"word{i}" for i in range(12_000))
    assert len(long_title) > 60_000
    rec = make_source(ledger, "a.txt", ["Some content about the desk."], date=exact("2001-06-01"), title=long_title)
    index.sync([rec])

    reply = index.search()
    assert estimate_tokens(reply.text()) <= REPLY_TOKEN_BUDGET
    assert [h.ref for h in reply.hits] == [rec.ref]  # the hit is still returned

    hit = reply.hits[0]
    line_title = hit.line().split("  ")[3]
    assert len(line_title) <= 120
    assert line_title == cap_at_word_boundary(long_title)


def test_full_title_stays_stored_and_searchable_past_the_display_cap(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    long_title = "Intro " + "filler " * 3000 + "uniquetail"
    rec = make_source(ledger, "a.txt", [long_title], date=exact("2001-06-01"), title=long_title)
    index.sync([rec])

    stored = index._conn.execute("SELECT title FROM records WHERE ref = ?", (rec.ref,)).fetchone()["title"]
    assert stored == long_title  # the cap is on display only

    reply = index.search("uniquetail")  # past character 120 of the title
    assert [h.ref for h in reply.hits] == [f"{rec.ref} p1"]


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
    big = _awkward_text()
    rec = make_source(ledger, "big.txt", ["Intro.", big, "Outro."], date=exact("2001-06-01"))
    index.sync([rec])
    pages, body = _read_pages(index, rec.ref)
    assert len(pages) > 1
    assert body == "Intro." + PARAGRAPH_SEPARATOR + big + PARAGRAPH_SEPARATOR + "Outro."
    for page in pages:
        assert rec.ref not in page.body  # transport labels never enter the payload
        assert "reply_tokens" not in page.body and "[continues" not in page.body


def test_oversized_note_reads_page_and_reconstruct_exactly(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    big = _awkward_text()
    note = make_note("notes/theme/big.md", ["Intro.", big], type="theme")
    index.sync([note])
    pages, body = _read_pages(index, note.ref)
    assert len(pages) > 1
    assert body == "Intro." + PARAGRAPH_SEPARATOR + big
    assert all(note.ref not in page.body for page in pages)


def test_oversized_manuscript_section_reads_page_and_reconstruct_exactly(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    big = _awkward_text()
    manuscript = make_manuscript("manuscript/ch99.md", ["# July 2001", "## The long week", big, "## After", "Short."])
    index.sync([manuscript])
    pages, body = _read_pages(index, "manuscript/ch99.md # The long week")
    assert len(pages) > 1
    assert body == "## The long week" + PARAGRAPH_SEPARATOR + big
    assert "Short." not in body


def test_pages_never_split_a_crlf_pair_or_a_combining_mark(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    big = _awkward_text(120_000)
    rec = make_source(ledger, "crlf.txt", [big], date=exact("2001-06-01"))
    index.sync([rec])
    pages, body = _read_pages(index, rec.ref)
    assert len(pages) > 2
    assert body == big
    import unicodedata

    for page in pages:
        assert not page.body.endswith("\r")
        assert not page.body.startswith("\n")
        assert unicodedata.combining(page.body[0]) == 0


def test_retired_text_reply_stays_bounded(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    v1 = make_source(ledger, "a.txt", ["Original wording, not too long."], date=exact("2001-06-01"))
    index.sync([v1])
    v2 = make_source(ledger, "a.txt", ["Changed wording, still short."], date=exact("2001-06-01"))
    index.sync([v2])
    reply = index.read(f"{v1.ref} p1")
    assert estimate_tokens(reply.text()) <= REPLY_TOKEN_BUDGET
    assert reply.body == "Original wording, not too long."


def test_an_oversized_retired_paragraph_pages_exactly(tmp_path, ledger):
    index = lexical_index(tmp_path, ledger)
    big = _awkward_text()
    v1 = make_source(ledger, "a.txt", [big], date=exact("2001-06-01"))
    index.sync([v1])
    v2 = make_source(ledger, "a.txt", ["Replaced."], date=exact("2001-06-01"))
    index.sync([v2])
    pages, body = _read_pages(index, f"{v1.ref} p1")
    assert len(pages) > 1
    assert body == big
    assert any("retired at v2" in label for label in pages[0].labels)


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

"""read(ref, cursor): any ref shape, paging at paragraph/character
boundaries, retired-anchor markers, note-warning markers (design.md "Read";
acceptance #22 tests 3 and 6).
"""

from __future__ import annotations

import pytest

from strata.embeddings import FakeEmbedder
from strata.index import BadRef, Index
from strata.ledger import Ledger

from factories import exact, make_manuscript, make_note, make_source


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    yield led
    led.close()


@pytest.fixture
def index(tmp_path, ledger):
    idx = Index(tmp_path / "index.db", ledger=ledger, embedder=FakeEmbedder(), semantic=False)
    yield idx
    idx.close()


def test_read_a_single_live_paragraph_anchor(index):
    ledger = index._ledger
    rec = make_source(ledger, "a.txt", ["First.", "Second paragraph text."], date=exact("2001-06-01"))
    index.sync([rec])
    reply = index.read(f"{rec.ref} p2")
    assert "Second paragraph text." in reply.body
    assert reply.continuation is None


def test_read_the_whole_record_concatenates_every_paragraph_in_order(index):
    ledger = index._ledger
    rec = make_source(ledger, "a.txt", ["First.", "Second.", "Third."], date=exact("2001-06-01"))
    index.sync([rec])
    reply = index.read(rec.ref)
    assert "First." in reply.body and "Second." in reply.body and "Third." in reply.body
    assert reply.body.index("First.") < reply.body.index("Second.") < reply.body.index("Third.")


def test_read_a_range_traverses_document_order(index):
    ledger = index._ledger
    rec = make_source(ledger, "a.txt", ["P1", "P2", "P3", "P4"], date=exact("2001-06-01"))
    index.sync([rec])
    reply = index.read(f"{rec.ref} p1-3")
    assert "P1" in reply.body and "P2" in reply.body and "P3" in reply.body
    assert "P4" not in reply.body


def test_read_a_retired_anchor_returns_the_ledgers_marker_and_exact_text(index):
    ledger = index._ledger
    v1 = make_source(ledger, "a.txt", ["Original wording."], date=exact("2001-06-01"))
    index.sync([v1])
    v2 = make_source(ledger, "a.txt", ["Changed wording."], date=exact("2001-06-01"))
    index.sync([v2])

    reply = index.read(f"{v1.ref} p1")
    assert "retired at v2" in reply.body
    assert "Original wording." in reply.body
    assert f"The record's current text is {v1.ref}." in reply.body


def test_read_a_note_with_warnings_shows_one_marker_line_per_warning(index):
    note = make_note(
        "notes/project.md", ["Body text."], warnings=("unknown frontmatter key: foo",), title="Project"
    )
    index.sync([note])
    reply = index.read(note.ref)
    assert "warning: unknown frontmatter key: foo" in reply.body
    assert "Body text." in reply.body


def test_a_note_with_no_warnings_shows_no_marker(index):
    note = make_note("notes/project.md", ["Body text."], title="Project")
    index.sync([note])
    reply = index.read(note.ref)
    assert "warning:" not in reply.body


def test_read_a_manuscript_heading_section_stops_at_the_next_same_depth_heading(index):
    manuscript = make_manuscript(
        "manuscript/ch01.md",
        [
            "# Chapter One",
            "## The Letter",
            "Section body one.",
            "## After",
            "Section body two.",
        ],
        title="Chapter One",
    )
    index.sync([manuscript])
    reply = index.read("manuscript/ch01.md # The Letter")
    assert "Section body one." in reply.body
    assert "Section body two." not in reply.body


def test_a_repeated_heading_uses_the_occurrence_suffix(index):
    manuscript = make_manuscript(
        "manuscript/ch01.md",
        ["## Later", "First instance.", "## Later", "Second instance."],
        title="Chapter One",
    )
    index.sync([manuscript])
    first = index.read("manuscript/ch01.md # Later")
    second = index.read("manuscript/ch01.md # Later (2)")
    assert "First instance." in first.body
    assert "Second instance." in second.body
    assert "Second instance." not in first.body


def test_reading_an_unknown_heading_raises_bad_ref(index):
    manuscript = make_manuscript("manuscript/ch01.md", ["## Real", "Text."], title="Chapter One")
    index.sync([manuscript])
    with pytest.raises(BadRef):
        index.read("manuscript/ch01.md # Not There")


def test_reading_a_missing_ref_raises_bad_ref(index):
    with pytest.raises(BadRef):
        index.read("SRC-000099")


def test_an_oversized_project_note_with_a_warning_opens_with_the_marker(index):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import make_fixtures as fx

    big = fx.oversized_paragraph(40_000)
    project = make_note(
        "notes/project.md",
        ["Project overview.", big],
        warnings=("project.md exceeds 2000 words",),
        title="Project",
    )
    index.sync([project])
    reply = index.read(project.ref)
    assert "warning: project.md exceeds 2000 words" in reply.body
    body = reply.body
    cursor = reply.continuation
    while cursor:
        page = index.read(cursor=cursor)
        body += page.body
        cursor = page.continuation
    assert big in body
    assert "Project overview." in body


def test_concatenating_read_pages_reproduces_the_exact_text(index):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import make_fixtures as fx

    big = fx.oversized_paragraph(20_000)
    ledger = index._ledger
    rec = make_source(ledger, "big.txt", ["Intro.", big, "Outro."], date=exact("2001-06-01"))
    index.sync([rec])

    reply = index.read(rec.ref)
    body = reply.body
    cursor = reply.continuation
    while cursor:
        nxt = index.read(cursor=cursor)
        body += nxt.body
        cursor = nxt.continuation

    assert big in body
    assert "Intro." in body
    assert "Outro." in body

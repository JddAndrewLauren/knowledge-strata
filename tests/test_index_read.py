"""read(ref, cursor): any ref shape, transport labels apart from the exact
payload, per-paragraph anchors on source reads, paging at paragraph or
character boundaries with the separator kept on the page that owns it,
retired-anchor markers, note-warning markers (design.md "Read"; acceptance
#22 tests 3 and 6).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from strata.embeddings import FakeEmbedder
from strata.index import PARAGRAPH_SEPARATOR, BadRef, Index
from strata.ledger import Ledger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import make_fixtures as fx  # noqa: E402

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


def _read_all(index, ref):
    """Every page of a read: the first page's labels, the concatenated
    payload, and the pages themselves."""
    reply = index.read(ref)
    pages = [reply]
    while reply.continuation:
        reply = index.read(cursor=reply.continuation)
        pages.append(reply)
    return pages[0].labels, "".join(page.body for page in pages), pages


def test_read_a_single_live_paragraph_anchor_labels_it_with_its_citation(index):
    ledger = index._ledger
    rec = make_source(ledger, "a.txt", ["First.", "Second paragraph text."], date=exact("2001-06-01"))
    index.sync([rec])
    reply = index.read(f"{rec.ref} p2")
    assert reply.labels == (f"{rec.ref} p2  2001-06-01  source  First.",)
    assert [piece.label for piece in reply.pieces] == [f"{rec.ref} p2"]
    assert reply.body == "Second paragraph text."
    assert reply.continuation is None


def test_read_the_whole_record_shows_every_paragraph_with_its_anchor(index):
    ledger = index._ledger
    rec = make_source(ledger, "a.txt", ["First.", "Second.", "Third."], date=exact("2001-06-01"))
    index.sync([rec])
    reply = index.read(rec.ref)
    assert reply.labels == (f"{rec.ref}  2001-06-01  source  First.",)
    assert [piece.label for piece in reply.pieces] == [f"{rec.ref} p1", f"{rec.ref} p2", f"{rec.ref} p3"]
    assert reply.body == "First.\n\nSecond.\n\nThird."
    text = reply.text()
    assert text.index(f"{rec.ref} p2\nSecond.") < text.index(f"{rec.ref} p3\nThird.")


def test_read_a_range_traverses_document_order_with_anchors(index):
    ledger = index._ledger
    rec = make_source(ledger, "a.txt", ["P1", "P2", "P3", "P4"], date=exact("2001-06-01"))
    index.sync([rec])
    reply = index.read(f"{rec.ref} p1-3")
    assert reply.labels[0].startswith(f"{rec.ref} p1-3  2001-06-01  source  ")
    assert [piece.label for piece in reply.pieces] == [f"{rec.ref} p1", f"{rec.ref} p2", f"{rec.ref} p3"]
    assert reply.body == "P1\n\nP2\n\nP3"


def test_a_range_with_a_retired_endpoint_returns_the_ledgers_diagnostic_as_a_label(index):
    ledger = index._ledger
    v1 = make_source(ledger, "a.txt", ["One.", "Two.", "Three."], date=exact("2001-06-01"))
    index.sync([v1])
    v2 = make_source(ledger, "a.txt", ["One.", "Two, changed.", "Three."], date=exact("2001-06-01"))
    index.sync([v2])
    reply = index.read(f"{v1.ref} p1-2")
    assert any("p2 is retired" in label for label in reply.labels)
    assert reply.body == ""


def test_read_a_retired_anchor_returns_the_ledgers_marker_and_exact_text(index):
    ledger = index._ledger
    v1 = make_source(ledger, "a.txt", ["Original wording."], date=exact("2001-06-01"))
    index.sync([v1])
    v2 = make_source(ledger, "a.txt", ["Changed wording."], date=exact("2001-06-01"))
    index.sync([v2])

    reply = index.read(f"{v1.ref} p1")
    assert any("retired at v2" in label for label in reply.labels)
    assert f"The record's current text is {v1.ref}." in reply.labels
    assert reply.body == "Original wording."  # exact text is the whole payload
    assert "retired" not in reply.body


def test_read_a_note_with_warnings_shows_one_marker_line_per_warning(index):
    note = make_note(
        "notes/project.md", ["Body text."], warnings=("unknown frontmatter key: foo",), title="Project"
    )
    index.sync([note])
    reply = index.read(note.ref)
    assert reply.labels == ("notes/project.md  undated  note  Project", "warning: unknown frontmatter key: foo")
    assert reply.body == "Body text."
    assert reply.text().startswith("notes/project.md  undated  note  Project\nwarning: unknown frontmatter key: foo\n\nBody text.")


def test_a_note_with_no_warnings_shows_no_marker(index):
    note = make_note("notes/project.md", ["Body text."], title="Project")
    index.sync([note])
    reply = index.read(note.ref)
    assert "warning:" not in reply.text()
    assert [piece.label for piece in reply.pieces] == [None]


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
    assert reply.labels == ("manuscript/ch01.md # The Letter  undated  manuscript  Chapter One",)
    assert reply.body == "## The Letter\n\nSection body one."


def test_a_repeated_heading_uses_the_occurrence_suffix(index):
    manuscript = make_manuscript(
        "manuscript/ch01.md",
        ["## Later", "First instance.", "## Later", "Second instance."],
        title="Chapter One",
    )
    index.sync([manuscript])
    first = index.read("manuscript/ch01.md # Later")
    second = index.read("manuscript/ch01.md # Later (2)")
    assert first.body == "## Later\n\nFirst instance."
    assert second.body == "## Later\n\nSecond instance."


def test_reading_an_unknown_heading_raises_bad_ref(index):
    manuscript = make_manuscript("manuscript/ch01.md", ["## Real", "Text."], title="Chapter One")
    index.sync([manuscript])
    with pytest.raises(BadRef):
        index.read("manuscript/ch01.md # Not There")


def test_reading_a_missing_ref_raises_bad_ref(index):
    with pytest.raises(BadRef):
        index.read("SRC-000099")


def test_an_oversized_project_note_with_a_warning_opens_with_the_marker(index):
    big = fx.oversized_paragraph(40_000)
    project = make_note(
        "notes/project.md",
        ["Project overview.", big],
        warnings=("project.md exceeds 2000 words",),
        title="Project",
    )
    index.sync([project])
    labels, body, pages = _read_all(index, project.ref)
    assert "warning: project.md exceeds 2000 words" in labels
    assert len(pages) > 1
    assert body == "Project overview.\n\n" + big
    assert all("warning:" not in page.body for page in pages)


def test_concatenating_read_pages_reproduces_the_exact_text(index):
    big = fx.oversized_paragraph(40_000)
    ledger = index._ledger
    rec = make_source(ledger, "big.txt", ["Intro.", big, "Outro."], date=exact("2001-06-01"))
    index.sync([rec])
    _labels, body, pages = _read_all(index, rec.ref)
    assert len(pages) > 1
    assert body == "Intro.\n\n" + big + "\n\nOutro."
    continued = [piece.label for page in pages[1:] for piece in page.pieces if piece.label and "(continued)" in piece.label]
    assert continued and all(label.startswith(f"{rec.ref} p2") for label in continued)


def test_a_page_break_at_a_paragraph_boundary_keeps_the_separator(index):
    """Sixty 1,000-byte paragraphs: the first page ends on a whole
    paragraph, so the separator after it must travel with that page for
    the concatenation to be exact."""
    ledger = index._ledger
    paragraphs = [f"{i:03d}" + "a" * 996 + "." for i in range(60)]
    rec = make_source(ledger, "many.txt", paragraphs, date=exact("2001-06-01"))
    index.sync([rec])
    _labels, body, pages = _read_all(index, rec.ref)
    assert len(pages) > 1
    assert pages[0].pieces[-1].separator == PARAGRAPH_SEPARATOR
    assert pages[0].body.endswith(PARAGRAPH_SEPARATOR)
    assert body == PARAGRAPH_SEPARATOR.join(paragraphs)
    assert [piece.label for piece in pages[0].pieces] == [f"{rec.ref} p{i + 1}" for i in range(len(pages[0].pieces))]

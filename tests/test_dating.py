"""The dating module: four strategies, first hit wins, partial dates with
granularity (wayfinder #8, issue #27). Every rung and every west-desk
fixture `scripts/make_fixtures.py` documents, plus the shapes no committed
fixture happens to exercise (two-digit years, a labelled ``Date:`` line, the
pdf XMP fallback), covered here with invented content."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest

from strata.dating import RawUnit, date
from strata.record import Date, Record

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "examples" / "west-desk" / "sources"
MANUSCRIPT = ROOT / "examples" / "west-desk" / "manuscript"

UNKNOWN = Date("", "unknown", "day", "")


def _source(relative: str) -> Date:
    return date(RawUnit(path=relative, kind="source", content=(SOURCES / relative).read_bytes()))


def _manuscript(relative: str) -> Date:
    return date(RawUnit(path=relative, kind="manuscript", content=(MANUSCRIPT / relative).read_bytes()))


# --- every real west-desk source, as scripts/make_fixtures.py documents ---

WEST_DESK_SOURCES = {
    # rung 1: transport headers
    "mail/plain.eml": ("2001-05-23", "exact", "day"),
    "mail/quoted-angle.eml": ("2001-06-05", "exact", "day"),
    "mail/outlook-original-message.eml": ("2001-06-20", "exact", "day"),
    "mail/interleaved.eml": ("2001-06-21", "exact", "day"),
    "mail/thread-parent.eml": ("2001-05-16", "exact", "day"),
    "mail/thread-child.eml": ("2001-05-17", "exact", "day"),
    "mail/attachment.eml": ("2001-05-23", "exact", "day"),
    "mail/quoted-printable-wrapped.eml": ("2001-11-08", "exact", "day"),
    "mail/display-names-only.eml": ("2002-01-14", "exact", "day"),
    "mail/empty-body.eml": ("2001-11-30", "exact", "day"),
    "mail/received-only.eml": ("2001-09-04", "inferred", "day"),
    # rung 2: first line / leading heading, beating path and metadata
    "2001/June/journal-outage-week.txt": ("2001-06-19", "exact", "day"),
    "memos/portland-call.txt": ("2001-06-04", "inferred", "day"),
    "memos/tie-figures.txt": ("2001-01-01", "inferred", "year"),
    # rung 3: path patterns
    "2001/May/desk-checklist.txt": ("2001-05-01", "inferred", "month"),
    "2001/November/settlement-notes.txt": ("2001-11-01", "inferred", "month"),
    "2002/desk-reorganisation.txt": ("2002-01-01", "inferred", "year"),
    "memos/2002-01-14-scheduling-change.txt": ("2002-01-14", "exact", "day"),
    # rung 4: document metadata
    "memos/desk-procedures.docx": ("2001-08-20", "inferred", "day"),
    "memos/outage-letter.docx": ("1998-03-15", "inferred", "day"),
    "memos/outage-notice.pdf": ("2002-02-05", "inferred", "day"),
    # nothing produced anywhere
    "memos/signed-statement-scan.pdf": ("", "unknown", "day"),
    "memos/glossary.txt": ("", "unknown", "day"),
}


@pytest.mark.parametrize("relative", WEST_DESK_SOURCES.keys())
def test_every_west_desk_source_dates_as_the_fixture_generator_documents(relative):
    result = _source(relative)
    assert (result.iso, result.confidence, result.granularity) == WEST_DESK_SOURCES[relative]


def test_the_binary_as_text_guard_fixture_produces_no_date():
    """Garbage bytes named ``.txt`` must not accidentally look like a date."""
    assert _source("memos/not-really-text.txt") == UNKNOWN


# --- manuscript: first line and path only ----------------------------------

MANUSCRIPT_CHAPTERS = {
    "ch01-the-desk.md": ("2001-04-01", "inferred", "month"),
    "ch02-the-cutoff.md": ("2001-05-16", "inferred", "day"),
    "ch03-the-outage.md": ("2001-06-19", "exact", "day"),
}


@pytest.mark.parametrize("relative", MANUSCRIPT_CHAPTERS.keys())
def test_manuscript_chapters_date_from_the_leading_heading(relative):
    result = _manuscript(relative)
    assert (result.iso, result.confidence, result.granularity) == MANUSCRIPT_CHAPTERS[relative]


def test_manuscript_never_reads_document_metadata():
    """A manuscript ``.md`` file never carries a docx zip, but the dispatch
    itself is the guarantee: ``date()`` never calls the metadata rung for
    ``kind="manuscript"``, so a docx's own created-date property could not
    leak into a chapter's date even by accident."""
    docx_bytes = (SOURCES / "memos/outage-letter.docx").read_bytes()
    result = date(RawUnit(path="manuscript/ch01-the-desk.md", kind="manuscript", content=docx_bytes))
    assert result == UNKNOWN


# --- the three first-line shapes, each in exact and inferred forms ---------

FIRST_LINE_SHAPES = {
    "iso-exact": (b"2001-06-19\n\nBody text.\n", "2001-06-19", "exact"),
    "iso-inferred-embedded": (b"Entry 2001-06-19 draft\n\nBody text.\n", "2001-06-19", "inferred"),
    "month-name-exact": (b"19 June 2001\n\nBody text.\n", "2001-06-19", "exact"),
    "month-name-inferred-embedded": (b"Notes from the call, 19 June 2001\n\nBody text.\n", "2001-06-19", "inferred"),
    "numeric-unambiguous-exact": (b"14/6/2001\n\nBody text.\n", "2001-06-14", "exact"),
    "numeric-unambiguous-inferred-embedded": (b"Filed 14/6/2001 today\n\nBody text.\n", "2001-06-14", "inferred"),
}


@pytest.mark.parametrize("content, iso, confidence", FIRST_LINE_SHAPES.values(), ids=FIRST_LINE_SHAPES.keys())
def test_each_first_line_shape_in_its_exact_and_inferred_forms(content, iso, confidence):
    result = date(RawUnit(path="sources/x.txt", kind="source", content=content))
    assert (result.iso, result.confidence, result.granularity) == (iso, confidence, "day")


# --- notes: never dated ------------------------------------------------------


def test_notes_always_date_unknown_whatever_their_content():
    content = b"2001-01-01\n\nThis note carries an unambiguous ISO date.\n"
    assert date(RawUnit(path="notes/person/x.md", kind="note", content=content)) == UNKNOWN


# --- first-hit-wins and text-beats-filename, two conflicting sources -------


def test_first_hit_wins_email_date_over_received():
    content = (
        b"Date: Wed, 23 May 2001 08:58:33 -0500\r\n"
        b"Received: from a by b; Tue, 4 Sep 2001 15:44:10 -0500\r\n"
        b"Subject: x\r\n\r\nbody\r\n"
    )
    result = date(RawUnit(path="mail/x.eml", kind="source", content=content))
    assert result == Date("2001-05-23", "exact", "day", "Wed, 23 May 2001 08:58:33 -0500")


def test_bad_date_header_falls_back_to_received():
    content = (
        b"Date: November 4, 2013\r\n"
        b"Received: from a by b; Tue, 4 Sep 2001 15:44:10 -0500\r\n"
        b"Subject: x\r\n\r\nbody\r\n"
    )
    result = date(RawUnit(path="mail/x.eml", kind="source", content=content))
    assert result == Date("2001-09-04", "inferred", "day", "Received Tue, 4 Sep 2001 15:44:10 -0500")


def test_both_date_and_received_absent_is_unknown():
    content = b"Message-ID: <x@example.com>\r\nSubject: x\r\n\r\nbody\r\n"
    assert date(RawUnit(path="mail/x.eml", kind="source", content=content)) == UNKNOWN


def test_the_first_line_beats_a_disagreeing_iso_filename():
    """`docs/design.md`, "Dating": the author's own words beat the filename."""
    content = b"1 January 2020\n\nBody text.\n"
    result = date(RawUnit(path="sources/2013-11-04-diary.txt", kind="source", content=content))
    assert result == Date("2020-01-01", "exact", "day", "1 January 2020")


def test_the_first_line_beats_a_disagreeing_date_folder():
    """The real fixture for this: journal-outage-week.txt sits in a June
    folder but is dated 19 June by its own first line."""
    result = _source("2001/June/journal-outage-week.txt")
    assert result.iso == "2001-06-19"


# --- shapes no committed fixture happens to carry ---------------------------


def test_a_two_digit_year_first_line_is_inferred_even_when_the_rest_is_unambiguous():
    content = b"4 June 01\n\nBody text.\n"
    result = date(RawUnit(path="sources/x.txt", kind="source", content=content))
    assert result == Date("2001-06-04", "inferred", "day", "4 June 01")


def test_a_labelled_date_line_within_the_first_five_lines_is_read():
    content = b"A short title\n\nDate: 19 June 2001\n\nThe rest of the body.\n"
    result = date(RawUnit(path="sources/x.txt", kind="source", content=content))
    assert result == Date("2001-06-19", "exact", "day", "19 June 2001")


def test_a_labelled_date_line_past_the_first_five_lines_is_not_read():
    content = b"\n".join([b"Line %d." % i for i in range(1, 6)] + [b"", b"Date: 19 June 2001", b""])
    result = date(RawUnit(path="sources/x.txt", kind="source", content=content))
    assert result == UNKNOWN


def test_pdf_xmp_create_date_is_the_fallback_when_info_has_none():
    pdf = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
        b"2 0 obj\n<< /Length 20 >>\nstream\nBT /F1 12 Tf (x) Tj ET\nendstream\nendobj\n"
        b"6 0 obj\n<< /Title (x) /Creator (Acrobat) /Producer (Acrobat) >>\nendobj\n"
        b"7 0 obj\n<< >>\nstream\n<xmp:CreateDate>2013-11-04T21:15:03Z</xmp:CreateDate>\nendstream\nendobj\n"
        b"trailer\n<< /Root 1 0 R /Info 6 0 R >>\n%%EOF\n"
    )
    result = date(RawUnit(path="memos/x.pdf", kind="source", content=pdf))
    assert result == Date("2013-11-04", "inferred", "day", "pdf XMP CreateDate 2013-11-04")


def test_docx_with_no_created_property_falls_through_to_unknown():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as archive:
        archive.writestr(
            "docProps/core.xml",
            '<?xml version="1.0"?><cp:coreProperties '
            'xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties"/>',
        )
    result = date(RawUnit(path="memos/x.docx", kind="source", content=buf.getvalue()))
    assert result == UNKNOWN


def test_a_corrupt_docx_falls_through_to_unknown_rather_than_raising():
    result = date(RawUnit(path="memos/x.docx", kind="source", content=b"not a zip file"))
    assert result == UNKNOWN


def test_a_scanner_named_in_producer_alone_is_unknown_even_with_a_creation_date():
    pdf = (
        b"%PDF-1.4\n6 0 obj\n<< /Title (x) /Creator (Acrobat) "
        b"/Producer (ScanSoft PaperPort 6.1) /CreationDate (D:20011203091200-06'00') >>\nendobj\n"
        b"trailer\n<< /Info 6 0 R >>\n%%EOF\n"
    )
    result = date(RawUnit(path="memos/x.pdf", kind="source", content=pdf))
    assert result == UNKNOWN


# --- invariants --------------------------------------------------------------


ALL_SOURCE_RESULTS = [_source(relative) for relative in WEST_DESK_SOURCES]
ALL_MANUSCRIPT_RESULTS = [_manuscript(relative) for relative in MANUSCRIPT_CHAPTERS]


@pytest.mark.parametrize("result", ALL_SOURCE_RESULTS + ALL_MANUSCRIPT_RESULTS)
def test_every_returned_date_satisfies_the_record_invariants(result):
    """Constructing it is the assertion: ``Date.__post_init__`` raises on any
    impossible combination, and a partial date's ``iso`` is the first of its
    period by construction."""
    Date(result.iso, result.confidence, result.granularity, result.text)
    if result.iso:
        record = Record(
            ref="SRC-000001", kind="source", date=result, title="x", paragraphs=("x",)
        )
        assert record.date == result


def test_a_partial_dates_iso_is_the_first_of_its_period():
    month = _source("2001/May/desk-checklist.txt")
    assert month.granularity == "month" and month.iso.endswith("-01") and month.iso.startswith("2001-05")
    year = _source("2002/desk-reorganisation.txt")
    assert year.granularity == "year" and year.iso == "2002-01-01"


def test_no_output_is_exact_at_anything_but_day_granularity():
    for result in ALL_SOURCE_RESULTS + ALL_MANUSCRIPT_RESULTS:
        if result.confidence == "exact":
            assert result.granularity == "day"


def test_the_module_never_reads_file_mtime():
    import inspect

    import strata.dating as dating_module

    source = inspect.getsource(dating_module)
    assert "import os" not in source
    assert "st_mtime" not in source
    assert "getmtime" not in source

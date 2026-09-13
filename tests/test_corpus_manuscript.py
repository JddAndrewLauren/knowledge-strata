"""The manuscript adapter: heading sections, occurrence suffixes, dating
by chapter, skipping non-markdown files and dotfiles (issue #29).
"""

from __future__ import annotations

import sys
from pathlib import Path

from strata.corpus.manuscript import Skip, read
from strata.record import Record

ROOT = Path(__file__).resolve().parent.parent
MANUSCRIPT = ROOT / "examples" / "west-desk" / "manuscript"

sys.path.insert(0, str(ROOT / "scripts"))
import make_fixtures  # noqa: E402


def _by_ref(records: tuple[Record, ...]) -> dict[str, Record]:
    return {record.ref: record for record in records}


def test_west_desk_yields_the_file_record_and_every_section_record():
    walk = read(MANUSCRIPT)
    assert walk.skipped == ()
    by_ref = _by_ref(walk.records)
    expected = {
        "manuscript/ch01-the-desk.md",
        "manuscript/ch01-the-desk.md # April 2001",
        "manuscript/ch01-the-desk.md # The room",
        "manuscript/ch01-the-desk.md # What the desk did",
        "manuscript/ch01-the-desk.md # The checklist",
        "manuscript/ch02-the-cutoff.md",
        "manuscript/ch02-the-cutoff.md # The week of 16 May 2001",
        "manuscript/ch02-the-cutoff.md # The eleven o'clock rule",
        "manuscript/ch02-the-cutoff.md # Morning",
        "manuscript/ch02-the-cutoff.md # The working group",
        "manuscript/ch02-the-cutoff.md # Morning (2)",
        "manuscript/ch02-the-cutoff.md # What June was going to be",
        "manuscript/ch03-the-outage.md",
        "manuscript/ch03-the-outage.md # 19 June 2001",
        "manuscript/ch03-the-outage.md # Ten past eleven",
        "manuscript/ch03-the-outage.md # Forty, then sixty",
        "manuscript/ch03-the-outage.md # The letter",
        "manuscript/ch03-the-outage.md # What the statement said",
    }
    assert set(by_ref) == expected


def test_repeated_heading_text_gets_an_occurrence_suffix_in_document_order():
    by_ref = _by_ref(read(MANUSCRIPT).records)
    first = by_ref["manuscript/ch02-the-cutoff.md # Morning"]
    second = by_ref["manuscript/ch02-the-cutoff.md # Morning (2)"]
    assert first.title == second.title == "Morning"
    assert any("The first Monday under the new rule" in p for p in first.paragraphs)
    assert any("The second heading called Morning is here on purpose" in p for p in second.paragraphs)


def test_a_section_stops_at_the_next_equal_or_shallower_heading():
    by_ref = _by_ref(read(MANUSCRIPT).records)
    section = by_ref["manuscript/ch01-the-desk.md # The room"]
    assert section.paragraphs[0] == "## The room"
    assert not any(p.startswith("## What the desk did") for p in section.paragraphs)


def test_the_top_level_heading_section_includes_every_nested_subheading():
    by_ref = _by_ref(read(MANUSCRIPT).records)
    chapter = by_ref["manuscript/ch01-the-desk.md # April 2001"]
    whole_file = by_ref["manuscript/ch01-the-desk.md"]
    assert chapter.paragraphs == whole_file.paragraphs
    assert any(p == "## The checklist" for p in chapter.paragraphs)


# --- dating: first-line and path rungs only, shared by every chapter's records --


def test_manuscript_dates_come_from_the_dating_module():
    by_ref = _by_ref(read(MANUSCRIPT).records)
    cases = {
        "manuscript/ch01-the-desk.md": ("2001-04-01", "inferred", "month"),
        "manuscript/ch02-the-cutoff.md": ("2001-05-16", "inferred", "day"),
        "manuscript/ch03-the-outage.md": ("2001-06-19", "exact", "day"),
    }
    for ref, (iso, confidence, granularity) in cases.items():
        record = by_ref[ref]
        assert (record.date.iso, record.date.confidence, record.date.granularity) == (iso, confidence, granularity)


def test_every_record_from_one_chapter_shares_its_date():
    by_ref = _by_ref(read(MANUSCRIPT).records)
    dates = {ref: record.date for ref, record in by_ref.items() if ref.startswith("manuscript/ch03-")}
    assert len(set(dates.values())) == 1


# --- skip: non-markdown, walk continues -------------------------------------


def test_a_non_markdown_file_is_skipped_with_a_reason_and_the_walk_continues(tmp_path):
    folder = tmp_path / "manuscript"
    folder.mkdir()
    (folder / "ch01.md").write_text("# One\n\nBody text.\n", encoding="utf-8")
    (folder / "ch02-scrivener-export.docx").write_bytes(b"not really a docx")

    walk = read(folder)

    assert walk.skipped == (Skip("manuscript/ch02-scrivener-export.docx", walk.skipped[0].reason),)
    assert "docx" in walk.skipped[0].reason
    by_ref = _by_ref(walk.records)
    assert "manuscript/ch01.md" in by_ref


def test_dotfiles_are_skipped_silently_like_the_sources_adapter(tmp_path):
    folder = tmp_path / "manuscript"
    (folder / ".obsidian").mkdir(parents=True)
    (folder / "ch01.md").write_text("# One\n\nBody text.\n", encoding="utf-8")
    (folder / ".DS_Store").write_bytes(b"\x00\x00\x00\x01Bud1")
    (folder / ".obsidian" / "workspace.md").write_text("# Not a chapter\n", encoding="utf-8")

    walk = read(folder)

    assert walk.skipped == ()
    assert set(_by_ref(walk.records)) == {"manuscript/ch01.md", "manuscript/ch01.md # One"}


# --- the generated oversized section converts with its text intact ---------


def test_the_generated_oversized_section_keeps_its_text_intact(tmp_path):
    make_fixtures.scale_oversized(tmp_path)
    big = make_fixtures.oversized_paragraph()
    walk = read(tmp_path / "manuscript")
    assert walk.skipped == ()
    by_ref = _by_ref(walk.records)
    section = by_ref["manuscript/ch99-oversized.md # The long week"]
    # split_paragraphs strips surrounding whitespace, like every other converter;
    # the check is that the ~40,000 characters stay in one unsplit paragraph.
    assert any(big.rstrip() in paragraph for paragraph in section.paragraphs)

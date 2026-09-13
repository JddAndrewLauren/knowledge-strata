"""The notes adapter: folder types, the frontmatter contract, warnings
instead of drops (issue #29).
"""

from __future__ import annotations

import sys
from pathlib import Path

from strata.corpus.notes import read
from strata.record import Record

ROOT = Path(__file__).resolve().parent.parent
NOTES = ROOT / "examples" / "west-desk" / "notes"

sys.path.insert(0, str(ROOT / "scripts"))
import make_fixtures  # noqa: E402


def _by_ref(records: tuple[Record, ...]) -> dict[str, Record]:
    return {record.ref: record for record in records}


# --- the whole west-desk demo project: no warnings anywhere ----------------


def test_west_desk_notes_yield_one_record_per_file_with_the_right_type():
    records = read(NOTES)
    types = {record.ref: record.type for record in records}
    assert types == {
        "notes/project.md": None,
        "notes/person/priya-venkataraman.md": "person",
        "notes/person/marcus-idowu.md": "person",
        "notes/person/corinne-baptiste.md": "person",
        "notes/digest/2001-04-01--2001-04-30.md": "digest",
        "notes/digest/2001-05-01--2001-05-31.md": "digest",
        "notes/digest/2001-05-01--2001-05-31-2.md": "digest",
        "notes/digest/2001-06-01--2001-06-30.md": "digest",
        "notes/digest/2001-07-01--2001-07-16.md": "digest",
        "notes/history/2001-spring.md": "history",
        "notes/summary/2001-04-01--2001-07-16.md": "summary",
        "notes/recovery/summer-2001-fan-out.md": "recovery",
        "notes/recovery/summer-2001-fan-out-completion.md": "recovery",
    }


def test_west_desk_notes_carry_no_warnings():
    for record in read(NOTES):
        assert record.warnings == (), record.ref


def test_west_desk_every_note_date_is_unknown():
    for record in read(NOTES):
        assert record.date.confidence == "unknown"
        assert record.date.iso == ""


def test_west_desk_aliases_are_as_listed():
    by_ref = _by_ref(read(NOTES))
    priya = by_ref["notes/person/priya-venkataraman.md"]
    assert priya.aliases == (
        "Priya", "PV", "Venkataraman, Priya", "Priya.Venkataraman@example.com", "PVENKATA",
    )
    assert by_ref["notes/history/2001-spring.md"].aliases == ()


def test_west_desk_window_is_on_every_complete_digest():
    # The July digest is split 1 of 2 and interrupted; #17 wrote it without
    # frontmatter, as docs/drafts/agents/strata-reader.md has a split or
    # interrupted assignment omit `window`. Whether it should carry one anyway
    # (checkbox 1 of #29 says "all five") is an open question on #29; this
    # test follows the fixture as written until that is decided.
    by_ref = _by_ref(read(NOTES))
    windows = {
        ref: record.window for ref, record in by_ref.items() if record.type == "digest"
    }
    assert windows == {
        "notes/digest/2001-04-01--2001-04-30.md": ("2001-04-01", "2001-04-30"),
        "notes/digest/2001-05-01--2001-05-31.md": ("2001-05-01", "2001-05-31"),
        "notes/digest/2001-05-01--2001-05-31-2.md": ("2001-05-01", "2001-05-31"),
        "notes/digest/2001-06-01--2001-06-30.md": ("2001-06-01", "2001-06-30"),
        "notes/digest/2001-07-01--2001-07-16.md": None,
    }


def test_west_desk_corpus_revision_and_coverage_only_on_the_three_eligible_digests():
    by_ref = _by_ref(read(NOTES))
    eligible = {
        "notes/digest/2001-04-01--2001-04-30.md",
        "notes/digest/2001-05-01--2001-05-31.md",
        "notes/digest/2001-05-01--2001-05-31-2.md",
    }
    for ref, record in by_ref.items():
        if ref in eligible:
            assert record.corpus_revision is not None, ref
            assert record.coverage_complete is True, ref
        else:
            assert record.corpus_revision is None, ref
            assert record.coverage_complete is None, ref


def test_project_note_title_is_the_h1_and_type_is_none():
    project = _by_ref(read(NOTES))["notes/project.md"]
    assert project.title == "west-desk"
    assert project.type is None


# --- title fallback: the filename stem when there is no H1 -----------------


def test_title_falls_back_to_the_filename_stem_without_an_h1(tmp_path):
    notes = tmp_path / "notes"
    (notes / "person").mkdir(parents=True)
    (notes / "person" / "no-heading.md").write_text("Just a body, no heading at all.\n", encoding="utf-8")
    record = _by_ref(read(notes))["notes/person/no-heading.md"]
    assert record.title == "no-heading"


# --- invalid frontmatter: a warning, never a drop ---------------------------


def _write(tmp_path: Path, relative: str, text: str) -> Path:
    path = tmp_path / "notes" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return tmp_path / "notes"


def test_unknown_frontmatter_key_warns_and_keeps_the_note(tmp_path):
    folder = _write(
        tmp_path, "person/dave.md",
        "---\nfrobnicate: yes\naliases: [Dave]\n---\n# Dave\n\nBody text.\n",
    )
    record = _by_ref(read(folder))["notes/person/dave.md"]
    assert record.title == "Dave"
    assert record.aliases == ("Dave",)
    assert record.warnings == ("notes/person/dave.md: unknown frontmatter field 'frobnicate'; ignored",)


def test_non_list_aliases_warns_and_clears_aliases(tmp_path):
    folder = _write(tmp_path, "person/dave.md", "---\naliases: Dave\n---\n# Dave\n\nBody text.\n")
    record = _by_ref(read(folder))["notes/person/dave.md"]
    assert record.aliases == ()
    assert record.warnings == ("notes/person/dave.md: aliases is not a list; cleared",)


def test_window_to_before_from_warns_and_clears_window(tmp_path):
    folder = _write(
        tmp_path, "digest/bad.md",
        "---\nwindow:\n  from: 2001-06-30\n  to: 2001-06-01\n---\n# Digest\n\nBody text.\n",
    )
    record = _by_ref(read(folder))["notes/digest/bad.md"]
    assert record.window is None
    assert record.warnings == ("notes/digest/bad.md: window is not a valid from/to pair; cleared",)


def test_coverage_complete_without_corpus_revision_warns_and_clears_it(tmp_path):
    folder = _write(
        tmp_path, "digest/bad.md",
        "---\nwindow:\n  from: 2001-06-01\n  to: 2001-06-30\ncoverage_complete: true\n---\n# Digest\n\nBody.\n",
    )
    record = _by_ref(read(folder))["notes/digest/bad.md"]
    assert record.window == ("2001-06-01", "2001-06-30")
    assert record.coverage_complete is None
    assert record.warnings == (
        "notes/digest/bad.md: coverage_complete is set without corpus_revision; cleared",
    )


def test_an_invalid_field_does_not_disturb_a_valid_sibling_field(tmp_path):
    folder = _write(
        tmp_path, "person/dave.md",
        "---\naliases: Dave\nwindow:\n  from: 2001-06-01\n  to: 2001-06-30\n---\n# Dave\n\nBody.\n",
    )
    record = _by_ref(read(folder))["notes/person/dave.md"]
    assert record.aliases == ()
    assert record.window == ("2001-06-01", "2001-06-30")
    assert record.warnings == ("notes/person/dave.md: aliases is not a list; cleared",)


def test_a_digest_only_field_on_another_type_is_cleared_alone(tmp_path):
    folder = _write(
        tmp_path, "person/dave.md",
        "---\naliases: [Dave, DV]\ncorpus_revision: rev-1\n---\n# Dave\n\nBody.\n",
    )
    record = _by_ref(read(folder))["notes/person/dave.md"]
    assert record.aliases == ("Dave", "DV")
    assert record.corpus_revision is None
    assert record.warnings == (
        "notes/person/dave.md: corpus_revision is a digest field; this note's type is 'person'; cleared",
    )


def test_coverage_complete_on_a_top_level_note_is_cleared_alone(tmp_path):
    folder = _write(tmp_path, "project.md", "---\ncoverage_complete: true\n---\n# west-desk\n\nBody.\n")
    record = _by_ref(read(folder))["notes/project.md"]
    assert record.coverage_complete is None
    assert record.warnings == (
        "notes/project.md: coverage_complete is a digest field; this note's type is None; cleared",
    )


def test_an_impossible_calendar_day_in_the_window_is_cleared_alone(tmp_path):
    folder = _write(
        tmp_path, "digest/bad.md",
        "---\nwindow:\n  from: '2001-13-45'\n  to: '2001-13-46'\naliases: [X]\n---\n# Digest\n\nBody.\n",
    )
    record = _by_ref(read(folder))["notes/digest/bad.md"]
    assert record.window is None
    assert record.aliases == ("X",)
    assert record.warnings == ("notes/digest/bad.md: window is not a valid from/to pair; cleared",)


def test_malformed_yaml_frontmatter_warns_and_keeps_the_body(tmp_path):
    folder = _write(tmp_path, "person/dave.md", "---\naliases: [unclosed\n---\n# Dave\n\nBody text.\n")
    record = _by_ref(read(folder))["notes/person/dave.md"]
    assert record.title == "Dave"
    assert record.paragraphs == ("# Dave", "Body text.")
    assert record.aliases == ()
    assert record.warnings == ("notes/person/dave.md: frontmatter is not valid YAML; ignored",)


def test_frontmatter_that_is_not_a_mapping_warns_and_keeps_the_body(tmp_path):
    folder = _write(tmp_path, "person/dave.md", "---\n- Dave\n- DV\n---\n# Dave\n\nBody text.\n")
    record = _by_ref(read(folder))["notes/person/dave.md"]
    assert record.paragraphs == ("# Dave", "Body text.")
    assert record.warnings == ("notes/person/dave.md: frontmatter is not a mapping of fields; ignored",)


# --- encoding: a cp1252 note is indexed, never an abort ---------------------


def test_a_cp1252_note_is_indexed_and_does_not_abort_the_walk(tmp_path):
    folder = _write(tmp_path, "person/anna.md", "# Anna\n\nPlain sibling.\n")
    (folder / "person" / "rene.md").write_bytes(b"# Ren\xe9\n\nCaf\xe9 note.\n")
    by_ref = _by_ref(read(folder))
    assert set(by_ref) == {"notes/person/anna.md", "notes/person/rene.md"}
    rene = by_ref["notes/person/rene.md"]
    assert rene.title == "Ren\u00e9"
    assert rene.paragraphs == ("# Ren\u00e9", "Caf\u00e9 note.")
    assert rene.warnings == ()


# --- the 2,000-word cap on project.md only ---------------------------------


def test_west_desk_project_note_is_under_the_cap_and_carries_no_warning():
    project = _by_ref(read(NOTES))["notes/project.md"]
    assert project.warnings == ()


def test_an_oversized_project_note_warns_naming_the_file_and_the_cap(tmp_path):
    body = "# west-desk\n\n" + " ".join(f"word{i}" for i in range(2100)) + "\n"
    folder = _write(tmp_path, "project.md", body)
    project = _by_ref(read(folder))["notes/project.md"]
    assert len(project.warnings) == 1
    assert "notes/project.md" in project.warnings[0]
    assert "2,000-word cap" in project.warnings[0]


def test_an_oversized_note_that_is_not_project_md_carries_no_word_cap_warning(tmp_path):
    body = "# Oversized theme\n\n" + " ".join(f"word{i}" for i in range(2100)) + "\n"
    folder = _write(tmp_path, "theme/oversized.md", body)
    record = _by_ref(read(folder))["notes/theme/oversized.md"]
    assert record.warnings == ()


# --- the generated oversized note converts with its text intact ------------


def test_the_generated_oversized_note_keeps_its_text_intact(tmp_path):
    paths = make_fixtures.scale_oversized(tmp_path)
    big = make_fixtures.oversized_paragraph()
    record = _by_ref(read(tmp_path / "notes"))["notes/theme/oversized.md"]
    # split_paragraphs strips surrounding whitespace, like every other converter;
    # the check is that the ~40,000 characters stay in one unsplit paragraph.
    assert any(big.rstrip() in paragraph for paragraph in record.paragraphs), "the oversized text was split"
    assert paths["note"].exists()


# --- dotfiles and hidden folders are passed over -----------------------------


def test_notes_under_a_hidden_folder_are_passed_over(tmp_path):
    notes = tmp_path / "notes"
    (notes / ".obsidian").mkdir(parents=True)
    (notes / "project.md").write_text("# Project\n\nOverview.\n", encoding="utf-8")
    (notes / ".obsidian" / "workspace.md").write_text("# Not a note\n", encoding="utf-8")
    (notes / ".draft.md").write_text("# Not a note either\n", encoding="utf-8")

    records = read(notes)

    assert [record.ref for record in records] == ["notes/project.md"]

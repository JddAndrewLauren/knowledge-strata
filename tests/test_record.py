"""Date and Record invariants (wayfinder #6, #7, #8): the valid table
constructs, each invalid row raises at construction. Strings only."""

import dataclasses

import pytest

from strata.record import Date, Record, first_sentence
from strata.refs import BadRef

EXACT = Date("2001-06-19", "exact", "day", "19 June 2001")
INFERRED_DAY = Date("2001-06-04", "inferred", "day", "Portland call, 4 June 2001")
MONTH = Date("2001-11-01", "inferred", "month", "2001-11 (folder)")
YEAR = Date("2001-01-01", "inferred", "year", "2001 (numeric date, day/month ambiguous)")
UNKNOWN = Date("", "unknown", "day", "")


@pytest.mark.parametrize("date", [EXACT, INFERRED_DAY, MONTH, YEAR, UNKNOWN], ids=["exact-day", "inferred-day", "inferred-month", "inferred-year", "unknown"])
def test_valid_dates_construct(date):
    assert Date(*dataclasses.astuple(date)) == date


INVALID_DATES = [
    ("unknown-with-iso", ("2001-06-19", "unknown", "day", "")),
    ("exact-without-iso", ("", "exact", "day", "19 June 2001")),
    ("inferred-without-iso", ("", "inferred", "month", "")),
    ("iso-month-only", ("2001-11", "inferred", "month", "")),
    ("iso-compact", ("20011101", "inferred", "month", "")),
    ("iso-month-13", ("2001-13-01", "inferred", "day", "")),
    ("iso-day-31-june", ("2001-06-31", "exact", "day", "")),
    ("exact-month-granularity", ("2001-06-01", "exact", "month", "")),
    ("exact-year-granularity", ("2001-01-01", "exact", "year", "")),
    ("month-not-first", ("2001-11-05", "inferred", "month", "")),
    ("year-not-january", ("2001-03-01", "inferred", "year", "")),
    ("year-not-first", ("2001-01-02", "inferred", "year", "")),
    ("bad-confidence", ("2001-06-19", "certain", "day", "")),
    ("bad-granularity", ("2001-06-19", "exact", "week", "")),
]


@pytest.mark.parametrize("fields", [row[1] for row in INVALID_DATES], ids=[row[0] for row in INVALID_DATES])
def test_invalid_dates_raise_at_construction(fields):
    with pytest.raises(ValueError):
        Date(*fields)


PARAGRAPHS = ("The tie stepped at ten past three.", "Nobody schedules across it.")

VALID_RECORDS = {
    "source": dict(ref="SRC-000184", kind="source", date=EXACT, title="Cascade tie congestion report", paragraphs=PARAGRAPHS),
    "top-level-note": dict(ref="notes/project.md", kind="note", date=UNKNOWN, title="west-desk", paragraphs=PARAGRAPHS),
    "person-note": dict(ref="notes/person/priya-venkataraman.md", kind="note", date=UNKNOWN, title="Priya Venkataraman", paragraphs=PARAGRAPHS, type="person", aliases=("Priya", "PV")),
    "legacy-digest": dict(ref="notes/digest/2001-06-01--2001-06-30.md", kind="note", date=UNKNOWN, title="Digest 2001-06", paragraphs=PARAGRAPHS, type="digest", window=("2001-06-01", "2001-06-30")),
    "eligible-digest": dict(ref="notes/digest/2001-05-01--2001-05-31.md", kind="note", date=UNKNOWN, title="Digest 2001-05", paragraphs=PARAGRAPHS, type="digest", window=("2001-05-01", "2001-05-31"), corpus_revision="rev-0004", coverage_complete=True),
    "incomplete-digest": dict(ref="notes/digest/2001-07-01--2001-07-16.md", kind="note", date=UNKNOWN, title="Digest 2001-07", paragraphs=PARAGRAPHS, type="digest", window=("2001-07-01", "2001-07-16"), corpus_revision="rev-0004", coverage_complete=False),
    "windowed-history-note": dict(ref="notes/history/2001-spring.md", kind="note", date=UNKNOWN, title="Spring 2001", paragraphs=PARAGRAPHS, type="history", window=("2001-04-01", "2001-04-01")),
    "coined-type-note": dict(ref="notes/place/portland.md", kind="note", date=UNKNOWN, title="Portland", paragraphs=PARAGRAPHS, type="place"),
    "note-with-warnings": dict(ref="notes/person/marcus-idowu.md", kind="note", date=UNKNOWN, title="Marcus Idowu", paragraphs=PARAGRAPHS, type="person", warnings=("notes/person/marcus-idowu.md: aliases is not a list; cleared",)),
    "manuscript-section": dict(ref="manuscript/ch02-the-cutoff.md # Morning (2)", kind="manuscript", date=MONTH, title="Morning", paragraphs=PARAGRAPHS),
    "manuscript-file": dict(ref="manuscript/ch02-the-cutoff.md", kind="manuscript", date=MONTH, title="The cutoff", paragraphs=PARAGRAPHS),
    "empty-paragraphs": dict(ref="SRC-000002", kind="source", date=UNKNOWN, title="SRC-000002", paragraphs=()),
}


@pytest.mark.parametrize("fields", VALID_RECORDS.values(), ids=VALID_RECORDS.keys())
def test_valid_records_construct(fields):
    record = Record(**fields)
    for name, value in fields.items():
        assert getattr(record, name) == value


INVALID_RECORDS = {
    "empty-title": dict(VALID_RECORDS["source"], title=""),
    "blank-title": dict(VALID_RECORDS["source"], title="  \t"),
    "bad-kind": dict(VALID_RECORDS["source"], kind="email"),
    "empty-ref": dict(VALID_RECORDS["source"], ref=""),
    "type-on-source": dict(VALID_RECORDS["source"], type="person"),
    "type-on-manuscript": dict(VALID_RECORDS["manuscript-file"], type="digest"),
    "aliases-on-source": dict(VALID_RECORDS["source"], aliases=("PV",)),
    "aliases-on-manuscript": dict(VALID_RECORDS["manuscript-section"], aliases=("PV",)),
    "window-on-source": dict(VALID_RECORDS["source"], window=("2001-06-01", "2001-06-30")),
    "window-on-manuscript": dict(VALID_RECORDS["manuscript-file"], window=("2001-06-01", "2001-06-30")),
    "warnings-on-source": dict(VALID_RECORDS["source"], warnings=("x",)),
    "warnings-on-manuscript": dict(VALID_RECORDS["manuscript-section"], warnings=("x",)),
    "corpus-revision-on-source": dict(VALID_RECORDS["source"], corpus_revision="rev-0004"),
    "corpus-revision-on-person": dict(VALID_RECORDS["person-note"], corpus_revision="rev-0004"),
    "coverage-on-top-level-note": dict(VALID_RECORDS["top-level-note"], coverage_complete=True),
    "coverage-on-manuscript": dict(VALID_RECORDS["manuscript-file"], coverage_complete=True),
    "empty-corpus-revision": dict(VALID_RECORDS["eligible-digest"], corpus_revision=""),
    "coverage-not-bool": dict(VALID_RECORDS["eligible-digest"], coverage_complete="yes"),
    "window-reversed": dict(VALID_RECORDS["legacy-digest"], window=("2001-06-30", "2001-06-01")),
    "window-bad-day": dict(VALID_RECORDS["legacy-digest"], window=("2001-06-01", "2001-06-31")),
    "window-month-only": dict(VALID_RECORDS["legacy-digest"], window=("2001-06", "2001-07")),
    "window-not-a-pair": dict(VALID_RECORDS["legacy-digest"], window=("2001-06-01",)),
    "window-a-string": dict(VALID_RECORDS["legacy-digest"], window="2001-06-01"),
    "type-with-slash": dict(VALID_RECORDS["person-note"], type="person/sub"),
    "type-uppercase": dict(VALID_RECORDS["person-note"], type="Person"),
    "type-empty": dict(VALID_RECORDS["person-note"], type=""),
    # ref shape follows kind, stored canonically
    "source-with-anchor": dict(VALID_RECORDS["source"], ref="SRC-000184 p17"),
    "source-with-range": dict(VALID_RECORDS["source"], ref="SRC-000184 p17-"),
    "source-with-path": dict(VALID_RECORDS["source"], ref="notes/x.md"),
    "source-not-canonical": dict(VALID_RECORDS["source"], ref="src-000184"),
    "note-with-source-ref": dict(VALID_RECORDS["top-level-note"], ref="SRC-000184"),
    "note-with-heading": dict(VALID_RECORDS["top-level-note"], ref="notes/project.md # Book"),
    "note-not-canonical": dict(VALID_RECORDS["top-level-note"], ref="./notes/project.md"),
    "note-escaping-path": dict(VALID_RECORDS["top-level-note"], ref="../x.md"),
    "manuscript-with-source-ref": dict(VALID_RECORDS["manuscript-file"], ref="SRC-000184"),
    "manuscript-not-canonical": dict(VALID_RECORDS["manuscript-section"], ref="manuscript/ch02-the-cutoff.md#Morning (2)"),
}


@pytest.mark.parametrize("fields", INVALID_RECORDS.values(), ids=INVALID_RECORDS.keys())
def test_invalid_records_raise_at_construction(fields):
    with pytest.raises(ValueError):
        Record(**fields)


def test_a_bad_ref_is_a_value_error_too():
    with pytest.raises(BadRef):
        Record(**dict(VALID_RECORDS["top-level-note"], ref="../x.md"))


def test_record_is_frozen_and_hashable():
    record = Record(**VALID_RECORDS["source"])
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.title = "x"
    assert hash(record) == hash(Record(**VALID_RECORDS["source"]))


# --- first_sentence -------------------------------------------------------

LONG = "The tie stepped at ten past three and nobody on the desk had scheduled across it because the checklist said the Friday run was moved after the Portland call and that was that"


def test_first_sentence_takes_the_first_sentence_of_the_first_non_empty_paragraph():
    assert first_sentence(("", "   ", "It stepped. Nobody moved.", "Later.")) == "It stepped."


@pytest.mark.parametrize("text, expected", [
    ("Really? Yes.", "Really?"),
    ("Stop! Now.", "Stop!"),
    ("No terminator here", "No terminator here"),
    ("Fig. 3 shows it", "Fig."),
    ("Version 2.1 shipped. Then 2.2.", "Version 2.1 shipped."),
    ("  spaced\tout\n text.  ", "spaced out text."),
    ('He said "no." Then he left.', 'He said "no."'),
    ("It failed (badly). Again.", "It failed (badly)."),
    ("She asked 'why?' twice.", "She asked 'why?'"),
    ("\u201cStop.\u201d He did.", "\u201cStop.\u201d"),
    ("Wait... then go.", "Wait..."),
    ('A "quoted" word. Then.', 'A "quoted" word.'),
])
def test_first_sentence_stops_at_the_first_terminator_followed_by_space_or_end(text, expected):
    assert first_sentence((text,)) == expected


def test_first_sentence_cuts_at_120_on_a_word_boundary():
    assert len(LONG) > 120
    title = first_sentence((LONG,))
    assert len(title) <= 120
    assert LONG.startswith(title)
    assert LONG[len(title)] == " ", "cut fell inside a word"


def test_first_sentence_hard_cuts_a_single_word_longer_than_the_cap():
    word = "x" * 300
    assert first_sentence((word,)) == "x" * 120
    assert first_sentence((word,), cap=10) == "x" * 10


def test_first_sentence_honours_the_cap_argument():
    assert first_sentence(("one two three four",), cap=9) == "one two"


def test_first_sentence_is_never_empty_when_any_paragraph_has_text():
    assert first_sentence(("", "x")) == "x"
    assert first_sentence(("", ".")) == "."


def test_first_sentence_is_empty_only_when_nothing_has_text():
    assert first_sentence(()) == ""
    assert first_sentence(("", "  ", "\n")) == ""


def test_first_sentence_degrades_identically_for_every_kind():
    """It sees paragraphs, not records: the same text titles a source, a
    note and a manuscript section alike."""
    titles = {
        first_sentence(Record(**dict(fields, paragraphs=(LONG,))).paragraphs)
        for fields in (VALID_RECORDS["source"], VALID_RECORDS["top-level-note"], VALID_RECORDS["manuscript-section"])
    }
    assert len(titles) == 1

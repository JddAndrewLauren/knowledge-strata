"""The ref grammar (wayfinder #6): every accepted shape parses to one value
and renders canonically; every rejected shape raises a BadRef that names the
offending part. Strings only, no files."""

import pytest

from strata.refs import BadRef, ManuscriptRef, PathRef, SourceRef, anchor, parse, render, split_anchor

P17 = SourceRef("SRC-000184", anchor=17)

ACCEPTED = [
    # (id, text, value, canonical)
    ("canonical", "SRC-000184 p17", P17, "SRC-000184 p17"),
    ("upper-p", "SRC-000184 P17", P17, "SRC-000184 p17"),
    ("no-space", "SRC-000184p17", P17, "SRC-000184 p17"),
    ("pilcrow", "SRC-000184 \u00b617", P17, "SRC-000184 p17"),
    ("bare-anchor", "src-000184-p17", P17, "SRC-000184 p17"),
    ("whole-record", "SRC-000184", SourceRef("SRC-000184"), "SRC-000184"),
    ("lower-whole-record", "src-000184", SourceRef("SRC-000184"), "SRC-000184"),
    ("range", "SRC-000184 p17-22", SourceRef("SRC-000184", 17, end=22), "SRC-000184 p17-22"),
    ("tail", "SRC-000184 p17-", SourceRef("SRC-000184", 17, tail=True), "SRC-000184 p17-"),
    ("padded", "  SRC-000184 p17  ", P17, "SRC-000184 p17"),
    ("heading", "manuscript/ch24.md # The Letter", ManuscriptRef("manuscript/ch24.md", "The Letter"), "manuscript/ch24.md # The Letter"),
    ("heading-tight", "manuscript/ch24.md#The Letter", ManuscriptRef("manuscript/ch24.md", "The Letter"), "manuscript/ch24.md # The Letter"),
    ("heading-occurrence", "manuscript/ch24.md # The Letter (2)", ManuscriptRef("manuscript/ch24.md", "The Letter", 2), "manuscript/ch24.md # The Letter (2)"),
    ("heading-first-occurrence", "manuscript/ch24.md # The Letter (1)", ManuscriptRef("manuscript/ch24.md", "The Letter"), "manuscript/ch24.md # The Letter"),
    ("heading-as-written", "manuscript/ch02.md # Morning, again: the cut-off?", ManuscriptRef("manuscript/ch02.md", "Morning, again: the cut-off?"), "manuscript/ch02.md # Morning, again: the cut-off?"),
    ("note", "notes/person/dave.md", PathRef("notes/person/dave.md"), "notes/person/dave.md"),
    ("project-note", "notes/project.md", PathRef("notes/project.md"), "notes/project.md"),
    ("manuscript-file", "manuscript/ch24.md", PathRef("manuscript/ch24.md"), "manuscript/ch24.md"),
]


@pytest.mark.parametrize("text, value, canonical", [row[1:] for row in ACCEPTED], ids=[row[0] for row in ACCEPTED])
def test_every_accepted_shape_parses_to_one_value_and_renders_canonically(text, value, canonical):
    assert parse(text) == value
    assert render(parse(text)) == canonical
    assert parse(canonical) == value, "the canonical form must parse back to the same value"


@pytest.mark.parametrize("value", [row[2] for row in ACCEPTED], ids=[row[0] for row in ACCEPTED])
def test_render_and_parse_round_trip(value):
    assert parse(render(value)) == value


def test_the_parser_never_guesses_a_kind():
    """A bare path is a PathRef whether it is a note or a manuscript file;
    deciding which is the resolver's job."""
    assert type(parse("notes/x.md")) is type(parse("manuscript/x.md")) is PathRef


REJECTED = [
    # (id, text, the part the message must name)
    ("short-id", "SRC-184 p17", "SRC-184"),
    ("bare-number", "SRC-000184 17", "'17'"),
    ("long-id", "SRC-0001845", "SRC-0001845"),
    ("p-without-number", "SRC-000184 p", "'p'"),
    ("three-part-range", "SRC-000184 p17-22-30", "p17-22-30"),
    ("range-without-start", "SRC-000184 p-22", "p-22"),
    ("prefix-only", "SRC-", "SRC-"),
    ("heading-on-source", "SRC-000184 # The Letter", "# The Letter"),
    ("empty", "", "empty"),
    ("blank", "   ", "empty"),
    ("empty-heading", "notes/x.md #", "empty heading"),
    ("zero-occurrence", "manuscript/ch24.md # The Letter (0)", "(0)"),
    ("only-occurrence", "manuscript/ch24.md # (2)", "(2)"),
]


@pytest.mark.parametrize("text, part", [row[1:] for row in REJECTED], ids=[row[0] for row in REJECTED])
def test_every_rejected_shape_raises_naming_the_part(text, part):
    with pytest.raises(BadRef) as error:
        parse(text)
    assert part in str(error.value)


REFUSED_PATHS = [
    # (id, text, the part the message must name)
    ("absolute", "/etc/passwd", "absolute"),
    ("drive-slash", "C:/x", "drive"),
    ("drive-bare", "C:x", "drive"),
    ("dot-dot", "..", "'..'"),
    ("leading-dot-dot", "../notes/x.md", "'..'"),
    ("inner-dot-dot", "notes/../x.md", "'..'"),
    ("backslash", "notes\\x.md", "separator"),
    ("backslash-dot-dot", "docs\\..\\x", "separator"),
    ("dot", ".", "not a file"),
    ("dot-slash", "./", "not a file"),
    ("absolute-heading", "/ms/ch24.md # The Letter", "absolute"),
]


@pytest.mark.parametrize("text, part", [row[1:] for row in REFUSED_PATHS], ids=[row[0] for row in REFUSED_PATHS])
def test_every_repository_path_refusal_raises_naming_the_part(text, part):
    with pytest.raises(BadRef) as error:
        parse(text)
    assert part in str(error.value)


def test_bad_ref_is_a_value_error():
    assert issubclass(BadRef, ValueError)


def test_anchor_is_the_single_source_of_the_anchor_form():
    assert anchor(17) == "p17"
    assert split_anchor("p17") == 17
    assert split_anchor("P17") == 17
    assert split_anchor("\u00b617") == 17
    assert split_anchor(anchor(1234)) == 1234
    assert render(SourceRef("SRC-000184", 17)).endswith(anchor(17))


@pytest.mark.parametrize("text", ["17", "", "p", "p17-22", "x17"])
def test_split_anchor_refuses_a_bare_number_and_non_anchors(text):
    with pytest.raises(BadRef):
        split_anchor(text)


@pytest.mark.parametrize("value", [row[2] for row in ACCEPTED], ids=[row[0] for row in ACCEPTED])
def test_render_is_ascii(value):
    render(value).encode("ascii")


def test_a_range_needs_a_start_and_is_closed_or_open_not_both():
    with pytest.raises(BadRef):
        SourceRef("SRC-000184", end=22)
    with pytest.raises(BadRef):
        SourceRef("SRC-000184", tail=True)
    with pytest.raises(BadRef):
        SourceRef("SRC-000184", 17, end=22, tail=True)


def test_range_order_is_not_this_modules_question():
    """Document order, not numeric order, decides a range (ADR-0001)."""
    assert parse("SRC-000184 p22-17") == SourceRef("SRC-000184", 22, end=17)

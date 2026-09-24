"""Reading a note checks its source citations against the ledger at the
moment of the read (issue #65): a ref that does not exist, a retired anchor
(alone or as a range endpoint) and a bare ref to a deleted source each draw
one ``warning:`` label; live refs draw none. Nothing is stored at sync, and
the note's text comes back exactly as stored.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from strata.embeddings import FakeEmbedder
from strata.index import PARAGRAPH_SEPARATOR, Index, estimate_tokens
from strata.ledger import Ledger

from factories import exact, make_note, make_source

ROOT = Path(__file__).resolve().parent.parent
SKILL_DRAFT = ROOT / "docs" / "drafts" / "skill" / "SKILL.md"
SKILL_ASSET = ROOT / "src" / "strata" / "assets" / "skill" / "SKILL.md"


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    yield led
    led.close()


def _index(tmp_path, ledger, **kwargs):
    return Index(tmp_path / "index.db", ledger=ledger, embedder=FakeEmbedder(), semantic=False, **kwargs)


@pytest.fixture
def index(tmp_path, ledger):
    idx = _index(tmp_path, ledger)
    yield idx
    idx.close()


def _source(ledger, paragraphs=("One.", "Two.", "Three.")):
    return make_source(ledger, "a.txt", list(paragraphs), date=exact("2001-06-01"))


def _warnings(reply):
    return [label for label in reply.labels if label.startswith("warning: ")]


def _citation_warnings(reply):
    return [label for label in _warnings(reply) if label.startswith("warning: citation ")]


def test_a_note_citing_a_source_that_does_not_exist_warns_and_keeps_its_text(index, ledger):
    src = _source(ledger)
    body = ["# Dave", "- 2001-06-14  Moved the call. (SRC-000999 p3)", f"- Also ({src.ref} p9)."]
    note = make_note("notes/person/dave.md", body, title="Dave")
    index.sync([src, note])

    reply = index.read(note.ref)
    assert _citation_warnings(reply) == [
        "warning: citation SRC-000999 p3 does not exist",
        f"warning: citation {src.ref} p9 does not exist",
    ]
    assert reply.body == PARAGRAPH_SEPARATOR.join(body)
    assert "warning" not in reply.body


def test_a_note_citing_an_unknown_bare_record_warns(index, ledger):
    src = _source(ledger)
    note = make_note("notes/person/dave.md", ["- Met. (SRC-000999)"], title="Dave")
    index.sync([src, note])
    assert _citation_warnings(index.read(note.ref)) == ["warning: citation SRC-000999 does not exist"]


def test_a_note_citing_a_retired_anchor_names_the_ref_and_its_retiring_version(index, ledger):
    v1 = _source(ledger)
    v2 = _source(ledger, ("One.", "Two, changed.", "Three."))
    note = make_note("notes/person/dave.md", [f"- Said two. ({v1.ref} p2)"], title="Dave")
    index.sync([v2, note])

    [warning] = _citation_warnings(index.read(note.ref))
    assert warning.startswith(f"warning: citation {v1.ref} p2 retired at v2")
    assert "recheck the claim" in warning


@pytest.mark.parametrize("cited", ["p1-2", "p2-3", "p2-"])
def test_a_retired_range_endpoint_names_the_range_the_endpoint_and_the_version(index, ledger, cited):
    v1 = _source(ledger)
    v2 = _source(ledger, ("One.", "Two, changed.", "Three."))
    note = make_note("notes/person/dave.md", [f"- A run. ({v1.ref} {cited})"], title="Dave")
    index.sync([v2, note])

    [warning] = _citation_warnings(index.read(note.ref))
    assert warning.startswith(f"warning: citation {v1.ref} {cited}: {v1.ref} p2 retired at v2")


def test_a_range_endpoint_that_never_existed_warns(index, ledger):
    src = _source(ledger)
    note = make_note("notes/person/dave.md", [f"- A run. ({src.ref} p2-9)"], title="Dave")
    index.sync([src, note])
    assert _citation_warnings(index.read(note.ref)) == [
        f"warning: citation {src.ref} p2-9: {src.ref} p9 does not exist"
    ]


def test_a_reversed_range_warns_by_document_order_not_by_number(index, ledger):
    _source(ledger)
    src = _source(ledger, ("One.", "Two.", "New.", "Three."))  # New. is p4, before p3
    note = make_note("notes/person/dave.md", [f"- Back. ({src.ref} p3-1)", f"- On. ({src.ref} p4-3)"], title="Dave")
    index.sync([src, note])
    assert _citation_warnings(index.read(note.ref)) == [
        f"warning: citation {src.ref} p3-1 is reversed: p3 comes after p1 in the source"
    ]


def test_a_citation_wrapped_between_id_and_anchor_is_still_checked(index, ledger):
    v1 = _source(ledger)
    v2 = _source(ledger, ("One.", "Two, changed.", "Three."))
    note = make_note("notes/person/dave.md", [f"- Said two, as the record shows ({v1.ref}\n  p2)."], title="Dave")
    index.sync([v2, note])

    [warning] = _citation_warnings(index.read(note.ref))
    assert warning.startswith(f"warning: citation {v1.ref} p2 retired at v2")


def test_a_note_citing_only_live_refs_gets_no_citation_warning(index, ledger):
    src = _source(ledger)
    note = make_note(
        "notes/person/dave.md",
        [f"- One. ({src.ref} p1)", f"- Run. ({src.ref} p1-3; {src.ref} p2-; {src.ref})", "- No refs here."],
        title="Dave",
    )
    index.sync([src, note])
    reply = index.read(note.ref)
    assert _warnings(reply) == []


def test_several_refs_in_one_parenthesis_are_each_checked(index, ledger):
    src = _source(ledger)
    note = make_note(
        "notes/person/dave.md", [f"- Both. ({src.ref} p1; SRC-000999 p1; {src.ref} p8-)"], title="Dave"
    )
    index.sync([src, note])
    assert _citation_warnings(index.read(note.ref)) == [
        "warning: citation SRC-000999 p1 does not exist",
        f"warning: citation {src.ref} p8-: {src.ref} p8 does not exist",
    ]


def test_the_same_bad_ref_cited_twice_warns_once(index, ledger):
    src = _source(ledger)
    note = make_note("notes/person/dave.md", ["- A. (SRC-000999 p1)", "- B. (SRC-000999 p1)"], title="Dave")
    index.sync([src, note])
    assert _citation_warnings(index.read(note.ref)) == ["warning: citation SRC-000999 p1 does not exist"]


def test_citation_warnings_follow_the_notes_adapter_warnings(index, ledger):
    src = _source(ledger)
    note = make_note(
        "notes/person/dave.md", ["- A. (SRC-000999 p1)"], warnings=("aliases is not a list; cleared",), title="Dave"
    )
    index.sync([src, note])
    assert _warnings(index.read(note.ref)) == [
        "warning: aliases is not a list; cleared",
        "warning: citation SRC-000999 p1 does not exist",
    ]


def test_an_unedited_note_gains_the_retired_warning_once_its_source_changes_and_the_project_refreshes(index, ledger):
    v1 = _source(ledger)
    note = make_note("notes/person/dave.md", [f"- Said two. ({v1.ref} p2)"], title="Dave")
    index.sync([v1, note])
    assert _citation_warnings(index.read(note.ref)) == []

    v2 = _source(ledger, ("One.", "Two, changed.", "Three."))
    index.sync([v2, note])  # the note itself is unchanged and not re-synced
    [warning] = _citation_warnings(index.read(note.ref))
    assert warning.startswith(f"warning: citation {v1.ref} p2 retired at v2")


def test_a_bare_ref_to_a_source_that_has_left_the_corpus_warns(index, ledger):
    src = _source(ledger)
    note = make_note("notes/person/dave.md", [f"- Met. ({src.ref})"], title="Dave")
    index.sync([src, note])
    assert _citation_warnings(index.read(note.ref)) == []

    ledger.retire_unit("a.txt", at="2001-07-01")
    index.sync([note])
    assert _citation_warnings(index.read(note.ref)) == [
        f"warning: citation {src.ref}: the source has left the corpus"
    ]


def test_source_and_manuscript_reads_carry_no_citation_warnings(index, ledger):
    src = _source(ledger, ("Cites SRC-000999 p1 in its own text.",))
    index.sync([src])
    assert _warnings(index.read(src.ref)) == []


def test_search_replies_carry_no_citation_warnings(index, ledger):
    src = _source(ledger)
    note = make_note("notes/person/dave.md", ["- Portland call. (SRC-000999 p1)"], title="Dave")
    index.sync([src, note])
    assert "does not exist" not in index.search("Portland").text()


def test_a_note_with_more_bad_citations_than_one_reply_holds_pages_every_warning(tmp_path, ledger):
    idx = _index(tmp_path, ledger, reply_token_budget=2000)
    try:
        src = _source(ledger)
        body = [f"- Claim {n}. (SRC-{n:06d} p{n})" for n in range(100, 500)]
        note = make_note("notes/person/dave.md", body, title="Dave")
        idx.sync([src, note])

        reply = idx.read(note.ref)
        pages = [reply]
        while reply.continuation:
            reply = idx.read(cursor=reply.continuation)
            pages.append(reply)

        assert len(pages) > 1
        assert all(estimate_tokens(page.text()) <= 2000 for page in pages)
        metadata = "".join(page.metadata or "" for page in pages)
        for n in range(100, 500):
            assert f"warning: citation SRC-{n:06d} p{n} does not exist" in metadata
        assert "".join(page.body for page in pages) == PARAGRAPH_SEPARATOR.join(body)
    finally:
        idx.close()


def test_the_skill_explains_each_citation_warning_and_the_packaged_copy_matches_the_draft():
    assert SKILL_ASSET.read_bytes() == SKILL_DRAFT.read_bytes()
    skill = " ".join(SKILL_DRAFT.read_text(encoding="utf-8").split())
    for wording in ("warning: citation", "does not exist", "retired at vN", "left the corpus", "reversed"):
        assert wording in skill

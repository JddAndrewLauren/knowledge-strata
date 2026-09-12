"""who: alias expansion through a note's title, filename stem and aliases,
unioned across every matching note; no match keeps the string alone (#10
resolution ss7)."""

from __future__ import annotations

import pytest

from strata.embeddings import FakeEmbedder
from strata.index import Index
from strata.ledger import Ledger

from factories import exact, make_note, make_source


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


def test_who_by_full_name_and_by_alias_reach_the_same_records(index):
    ledger = index._ledger
    note = make_note(
        "notes/person/priya-venkataraman.md",
        ["Priya Venkataraman works the west desk."],
        type="person",
        aliases=("Priya", "PV", "Priya.Venkataraman@example.com"),
        title="Priya Venkataraman",
    )
    email = make_source(
        ledger, "email1.eml", ["From: Priya.Venkataraman@example.com\n\nPV called about the outage."], date=exact("2001-06-01")
    )
    unrelated = make_source(ledger, "other.txt", ["Nothing to do with anyone named here."], date=exact("2001-06-02"))
    index.sync([note, email, unrelated])

    by_full_name = {h.ref for h in index.search(who="Priya Venkataraman").hits}
    by_alias = {h.ref for h in index.search(who="PV").hits}
    assert by_full_name == by_alias
    assert email.ref in by_full_name
    assert note.ref in by_full_name
    assert unrelated.ref not in by_full_name


def test_who_by_stem_matches_the_notes_filename(index):
    note = make_note("notes/person/marcus-idowu.md", ["Marcus runs scheduling."], type="person", title="Marcus Idowu")
    index.sync([note])
    hits = {h.ref for h in index.search(who="marcus-idowu").hits}
    assert note.ref in hits


def test_who_with_no_matching_note_matches_only_the_literal_string(index):
    ledger = index._ledger
    rec = make_source(ledger, "a.txt", ["Dave mentioned the schedule."], date=exact("2001-06-01"))
    other = make_source(ledger, "b.txt", ["No relevant name."], date=exact("2001-06-02"))
    index.sync([rec, other])
    hits = {h.ref for h in index.search(who="Dave").hits}
    assert hits == {rec.ref}

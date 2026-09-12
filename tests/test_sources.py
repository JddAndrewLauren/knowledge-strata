"""The sources adapter: one raw unit per file, cached conversions, ledger ids
(design.md, "Sources adapter"; wayfinder #21, issue #30).

Every scenario is hermetic: a temporary ledger, a temporary conversion cache
(or, where the checkbox names it explicitly, a temporary ``$HOME`` standing
in for ``~/.strata/cache/``), and a throwaway copy of the demo project's
``sources/`` for the scenarios that edit, delete or add a file - the
committed fixtures are never written to.
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import pytest

from strata.corpus import sources
from strata.ledger import Ledger

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "examples" / "west-desk" / "sources"

sys.path.insert(0, str(ROOT / "scripts"))
import make_fixtures  # noqa: E402

sys.path.insert(0, str(ROOT / "tests"))
from test_dating import WEST_DESK_SOURCES  # noqa: E402

# The two files in the demo corpus that produce no Record.
NOT_TEXT = "memos/not-really-text.txt"
SCAN_NO_TEXT_LAYER = "memos/signed-statement-scan.pdf"


def _total_files(root: Path) -> int:
    return sum(1 for p in root.rglob("*") if p.is_file())


# -- acceptance #30 test 1: one Record per convertible file, ids in order --


def test_walking_west_desk_yields_one_record_per_convertible_file_with_a_correct_report(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    report = sources.sync([SOURCES], ledger, cache_db=tmp_path / "store.db")

    assert len(report.records) == _total_files(SOURCES) - 2  # NOT_TEXT, SCAN_NO_TEXT_LAYER

    reasons = {skip.path: skip.reason for skip in report.skipped}
    assert set(reasons) == {NOT_TEXT, SCAN_NO_TEXT_LAYER}
    assert not any("xls" in path for path in reasons)  # the attachment part is not a unit

    ids = sorted(int(record.ref.removeprefix("SRC-")) for record in report.records)
    assert ids == list(range(1, len(report.records) + 1))
    assert not report.deleted


# -- acceptance #30 test 2: an unchanged walk converts nothing -------------


def test_a_second_unchanged_walk_converts_nothing_and_matches_the_first(tmp_path, monkeypatch):
    ledger = Ledger(tmp_path / "ledger.db")
    cache_db = tmp_path / "store.db"
    first = sources.sync([SOURCES], ledger, cache_db=cache_db)

    def boom(*args, **kwargs):
        raise AssertionError("normalizer.normalize() must not run on a cache hit")

    monkeypatch.setattr(sources.normalizer, "normalize", boom)
    second = sources.sync([SOURCES], ledger, cache_db=cache_db)

    key = lambda records: {r.ref: (r.title, r.paragraphs, r.date) for r in records}  # noqa: E731
    assert key(second.records) == key(first.records)
    assert {s.path for s in second.skipped} == {s.path for s in first.skipped}
    assert not second.deleted


# -- acceptance #30 test 3: edit versions one unit; delete retires; --------
# -- adding a file that sorts first renumbers nothing ----------------------


def test_editing_deleting_and_adding_files_touch_only_whats_affected(tmp_path):
    corpus = tmp_path / "sources"
    shutil.copytree(SOURCES, corpus)
    ledger = Ledger(tmp_path / "ledger.db")
    cache_db = tmp_path / "store.db"

    first = sources.sync([corpus], ledger, cache_db=cache_db)
    units_before = ledger.known_units()
    journal_key = "000/2001/June/journal-outage-week.txt"
    glossary_key = "000/memos/glossary.txt"
    journal_id = units_before[journal_key][0]
    glossary_id = units_before[glossary_key][0]

    other_records_before = {r.ref: r for r in first.records if r.ref not in (journal_id, glossary_id)}

    # Edit: only the journal's unit gets a new version.
    journal_path = corpus / "2001" / "June" / "journal-outage-week.txt"
    journal_path.write_text(
        journal_path.read_text(encoding="utf-8").replace(
            "Tomas held the noon submission. First time I had seen him hold anything.",
            "Tomas held the noon submission, reluctantly.",
        ),
        encoding="utf-8",
    )
    second = sources.sync([corpus], ledger, cache_db=cache_db)
    second_by_ref = {r.ref: r for r in second.records}
    assert "Tomas held the noon submission, reluctantly." in second_by_ref[journal_id].paragraphs
    for ref, record in other_records_before.items():
        assert second_by_ref[ref] == record  # untouched

    # Delete: glossary's unit retires; its anchors keep their exact text.
    (corpus / "memos" / "glossary.txt").unlink()
    third = sources.sync([corpus], ledger, cache_db=cache_db)
    assert glossary_id in third.deleted
    assert glossary_id not in {r.ref for r in third.records}
    _, deleted_flag = ledger.known_units()[glossary_key]
    assert deleted_flag
    assert "retired" in ledger.text(f"{glossary_id} p1")
    assert "Desk glossary" in ledger.text(f"{glossary_id} p1")

    # Add a file that sorts before every existing path: nothing renumbers.
    (corpus / "0-first.txt").write_text("A new first file\n\nSomething to say.\n", encoding="utf-8")
    fourth = sources.sync([corpus], ledger, cache_db=cache_db)
    units_after = ledger.known_units()
    for path, (unit_id, _) in units_before.items():
        assert units_after[path][0] == unit_id  # no existing id moved
    new_id = units_after["000/0-first.txt"][0]
    highest_before = max(int(unit_id.removeprefix("SRC-")) for unit_id, _ in units_before.values())
    assert int(new_id.removeprefix("SRC-")) > highest_before
    assert any(r.ref == new_id for r in fourth.records)


# -- acceptance #30 test 4: deleting the user cache reconverts but replays -


def test_the_default_cache_lives_under_the_users_home(tmp_path, monkeypatch):
    """The behaviour test below drives ``sync`` through an explicit
    ``cache_db`` (portable across platforms' ``$HOME``/``%USERPROFILE%``
    resolution); this one checks the default path itself, directly."""
    monkeypatch.setattr(sources.Path, "home", lambda: tmp_path)
    assert sources._default_cache_db() == tmp_path / ".strata" / "cache" / "store.db"


def test_deleting_the_conversion_cache_reconverts_but_the_ledger_replays_every_anchor(tmp_path):
    corpus = tmp_path / "sources"
    shutil.copytree(SOURCES, corpus)
    ledger = Ledger(tmp_path / "ledger.db")
    cache_db = tmp_path / "cache" / "store.db"  # stands in for ~/.strata/cache/store.db

    first = sources.sync([corpus], ledger, cache_db=cache_db)
    assert cache_db.exists()
    units_before = ledger.known_units()

    shutil.rmtree(cache_db.parent)
    second = sources.sync([corpus], ledger, cache_db=cache_db)  # reconverts everything
    assert ledger.known_units() == units_before  # no ids added, changed or lost

    by_ref = lambda records: {r.ref: (r.title, r.paragraphs, r.date) for r in records}  # noqa: E731
    assert by_ref(second.records) == by_ref(first.records)

    # A further change after cache deletion still retires with exact text.
    journal_path = corpus / "2001" / "June" / "journal-outage-week.txt"
    journal_id = units_before["000/2001/June/journal-outage-week.txt"][0]
    journal_path.write_text(
        journal_path.read_text(encoding="utf-8").replace(
            "Tomas held the noon submission. First time I had seen him hold anything.",
            "Tomas held the noon submission, reluctantly.",
        ),
        encoding="utf-8",
    )
    sources.sync([corpus], ledger, cache_db=cache_db)
    retired_text = ledger.text(f"{journal_id} p3")
    assert "retired" in retired_text
    assert "First time I had seen him hold anything." in retired_text


# -- acceptance #30 test 5: two roots naming the same folder -----------------


def test_two_roots_naming_the_same_folder_produce_one_unit_per_file(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    report = sources.sync([SOURCES, str(SOURCES)], ledger, cache_db=tmp_path / "store.db")
    assert len(report.records) + len(report.skipped) == _total_files(SOURCES)


# -- acceptance #30 test 6: dates match the dating table, through this adapter -


def test_every_record_dates_as_the_dating_table_documents(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    report = sources.sync([SOURCES], ledger, cache_db=tmp_path / "store.db")
    units = ledger.known_units()
    records_by_ref = {r.ref: r for r in report.records}

    checked = 0
    for relative, expected in WEST_DESK_SOURCES.items():
        if relative == SCAN_NO_TEXT_LAYER:
            continue  # no text layer: produces no Record at all
        unit_id, _ = units[f"000/{relative}"]
        record = records_by_ref[unit_id]
        assert (record.date.iso, record.date.confidence, record.date.granularity) == expected, relative
        checked += 1
    assert checked == len(WEST_DESK_SOURCES) - 1


# -- acceptance #30 test 7: scale, in bounded time, no id gaps ---------------


@pytest.mark.parametrize(
    "make_root",
    [
        lambda root: make_fixtures.scale_busy_day(root),
        lambda root: make_fixtures.scale_undated(root),
    ],
    ids=["scale_busy_day", "scale_undated"],
)
def test_scale_walks_pass_500_records_with_no_id_gaps_in_bounded_time(tmp_path, make_root):
    root = tmp_path / "corpus"
    make_root(root)
    ledger = Ledger(tmp_path / "ledger.db")

    start = time.monotonic()
    report = sources.sync([root], ledger, cache_db=tmp_path / "store.db")
    elapsed = time.monotonic() - start

    assert len(report.records) > 500
    assert not report.skipped
    ids = sorted(int(record.ref.removeprefix("SRC-")) for record in report.records)
    assert ids == list(range(1, len(ids) + 1))  # no id gaps
    assert elapsed < 60, f"sync of {len(report.records)} records took {elapsed:.1f}s"

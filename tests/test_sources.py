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
from strata.ledger import AlignResult, Ledger

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

    # Assigned in sorted path order: the n-th path is SRC-00000n.
    units_by_path = sorted(ledger.known_units().items())
    assert len(units_by_path) == len(report.records)
    assert [unit_id for _, (unit_id, _) in units_by_path] == [
        f"SRC-{n:06d}" for n in range(1, len(units_by_path) + 1)
    ]


# -- acceptance #30 test 2: an unchanged walk converts nothing -------------


def test_a_second_unchanged_walk_converts_nothing_and_matches_the_first(tmp_path, monkeypatch):
    ledger = Ledger(tmp_path / "ledger.db")
    cache_db = tmp_path / "store.db"

    aligned: list[list[AlignResult]] = []  # one list of align outcomes per walk
    real_align = ledger.align

    def spying_align(*args, **kwargs):
        result = real_align(*args, **kwargs)
        aligned[-1].append(result)
        return result

    monkeypatch.setattr(ledger, "align", spying_align)

    aligned.append([])
    first = sources.sync([SOURCES], ledger, cache_db=cache_db)
    revision = ledger.corpus_revision()

    def boom(*args, **kwargs):
        raise AssertionError("normalizer.normalize() must not run on a cache hit")

    monkeypatch.setattr(sources.normalizer, "normalize", boom)
    aligned.append([])
    second = sources.sync([SOURCES], ledger, cache_db=cache_db)

    key = lambda records: {r.ref: (r.title, r.paragraphs, r.date) for r in records}  # noqa: E731
    assert key(second.records) == key(first.records)
    assert {s.path for s in second.skipped} == {s.path for s in first.skipped}
    assert not second.deleted

    # Identical versions: every unit's align was a no-op at the same version.
    versions = lambda results: {r.id: r.version for r in results}  # noqa: E731
    assert versions(aligned[1]) == versions(aligned[0])
    assert all(not r.changed for r in aligned[1])
    assert ledger.corpus_revision() == revision


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


# -- review round 1: a unit that stops converting leaves the index --------
# -- as a membership change, not silently ----------------------------------


def test_a_unit_that_stops_converting_retires_with_exact_text_and_advances_the_revision(tmp_path):
    corpus = tmp_path / "sources"
    shutil.copytree(SOURCES, corpus)
    ledger = Ledger(tmp_path / "ledger.db")
    cache_db = tmp_path / "store.db"

    sources.sync([corpus], ledger, cache_db=cache_db)
    glossary_key = "000/memos/glossary.txt"
    glossary_id, _ = ledger.known_units()[glossary_key]
    revision = ledger.corpus_revision()

    # The file is still there, but now the normalizer refuses it.
    (corpus / "memos" / "glossary.txt").write_bytes(b"\x00\x01\x02 not text any more \x00")
    second = sources.sync([corpus], ledger, cache_db=cache_db)

    assert "memos/glossary.txt" in {skip.path for skip in second.skipped}
    assert glossary_id not in {r.ref for r in second.records}
    assert glossary_id in second.deleted
    assert ledger.known_units()[glossary_key] == (glossary_id, True)
    retired_text = ledger.text(f"{glossary_id} p1")
    assert "retired" in retired_text
    assert "Desk glossary" in retired_text
    assert ledger.corpus_revision() != revision

    # A third walk with the file still refused retires nothing again.
    revision = ledger.corpus_revision()
    third = sources.sync([corpus], ledger, cache_db=cache_db)
    assert not third.deleted
    assert ledger.corpus_revision() == revision
    assert {r.ref for r in third.records} == {r.ref for r in second.records}

    # Converting again resumes the same unit at a new version, not a new id.
    (corpus / "memos" / "glossary.txt").write_text("Desk glossary\n\nRestored.\n", encoding="utf-8")
    fourth = sources.sync([corpus], ledger, cache_db=cache_db)
    assert glossary_id in {r.ref for r in fourth.records}
    assert ledger.known_units()[glossary_key] == (glossary_id, False)
    assert not fourth.deleted


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


# -- issue #43: an empty-Subject email's title falls back to its own ref ----


def test_an_empty_subject_emails_record_title_equals_its_rendered_ref(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    report = sources.sync([SOURCES], ledger, cache_db=tmp_path / "store.db")
    units = ledger.known_units()
    unit_id, _ = units["000/mail/empty-body.eml"]
    record = next(r for r in report.records if r.ref == unit_id)
    assert record.title == record.ref


# -- issue #43: one suffix map, owned by the normalizer ----------------------


def test_the_adapter_defines_no_suffix_literal_map():
    assert not hasattr(sources, "_CONVERTER_NAMES")


def test_an_unclaimed_suffixs_skip_reason_is_unchanged(tmp_path):
    corpus = tmp_path / "sources"
    corpus.mkdir()
    (corpus / "export.mbox").write_bytes(b"whatever")
    ledger = Ledger(tmp_path / "ledger.db")
    report = sources.sync([corpus], ledger, cache_db=tmp_path / "store.db")
    assert report.skipped == (sources.Skip("export.mbox", "no converter claims the suffix '.mbox'"),)


# -- issue #45: the versioned converter id lands in versions.converter ------


def test_the_versioned_converter_id_lands_in_versions_converter(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    sources.sync([SOURCES], ledger, cache_db=tmp_path / "store.db")
    units = ledger.known_units()

    seen_suffixes = set()
    for path, (unit_id, _) in units.items():
        suffix = Path(path).suffix.lower()
        name = sources.normalizer.CONVERTER_IDS.get(suffix)
        if name is None:
            continue
        n = ledger._last_version(unit_id)
        row = ledger._conn.execute(
            "SELECT converter FROM versions WHERE unit_id = ? AND n = ?", (unit_id, n)
        ).fetchone()
        assert row["converter"] == f"{name}@{sources.normalizer.CONVERTER_VERSIONS[name]}"
        seen_suffixes.add(suffix)
    assert seen_suffixes == {".txt", ".docx", ".pdf", ".eml"}  # west-desk carries no .md


# -- issue #45: bumping a converter version reconverts only its own units ---


def test_bumping_a_converter_version_reconverts_only_that_converters_units(tmp_path, monkeypatch):
    ledger = Ledger(tmp_path / "ledger.db")
    cache_db = tmp_path / "store.db"
    sources.sync([SOURCES], ledger, cache_db=cache_db)
    revision = ledger.corpus_revision()
    units = ledger.known_units()

    versions_before = {unit_id: ledger._last_version(unit_id) for _, (unit_id, _) in units.items()}
    anchors_before = {unit_id: ledger.live_anchors(unit_id) for _, (unit_id, _) in units.items()}
    expected_reconverted = {
        str(p.relative_to(SOURCES)).replace("\\", "/") for p in SOURCES.rglob("*.txt") if p.is_file()
    }

    calls: list[str] = []
    real_normalize = sources.normalizer.normalize

    def spying_normalize(raw_bytes, path):
        calls.append(path)
        return real_normalize(raw_bytes, path)

    monkeypatch.setattr(sources.normalizer, "normalize", spying_normalize)
    monkeypatch.setitem(
        sources.normalizer.CONVERTER_VERSIONS, "text", sources.normalizer.CONVERTER_VERSIONS["text"] + 1
    )

    sources.sync([SOURCES], ledger, cache_db=cache_db)

    assert set(calls) == expected_reconverted  # only .txt units re-converted; others served from cache

    for path, (unit_id, _) in units.items():
        if path.endswith(".txt"):
            assert ledger._last_version(unit_id) == versions_before[unit_id] + 1  # re-versioned
        else:
            assert ledger._last_version(unit_id) == versions_before[unit_id]  # untouched
        # Unchanged paragraphs keep their exact anchor numbers; nothing retires.
        assert ledger.live_anchors(unit_id) == anchors_before[unit_id]

    assert ledger.corpus_revision() != revision


# -- issue #45: bumping the dating version re-versions everything, no reconvert -


def test_bumping_the_dating_version_re_versions_every_unit_without_reconverting(tmp_path, monkeypatch):
    from strata import dating

    ledger = Ledger(tmp_path / "ledger.db")
    cache_db = tmp_path / "store.db"
    sources.sync([SOURCES], ledger, cache_db=cache_db)
    units = ledger.known_units()
    versions_before = {unit_id: ledger._last_version(unit_id) for _, (unit_id, _) in units.items()}
    anchors_before = {unit_id: ledger.live_anchors(unit_id) for _, (unit_id, _) in units.items()}
    revision = ledger.corpus_revision()

    def boom(*args, **kwargs):
        raise AssertionError("normalizer.normalize() must not run on a dating-only bump")

    monkeypatch.setattr(sources.normalizer, "normalize", boom)
    monkeypatch.setattr(dating, "DATING_VERSION", dating.DATING_VERSION + 1)

    sources.sync([SOURCES], ledger, cache_db=cache_db)

    for unit_id, before in versions_before.items():
        assert ledger._last_version(unit_id) == before + 1  # every unit re-versioned
        assert ledger.live_anchors(unit_id) == anchors_before[unit_id]  # nothing retires
    assert ledger.corpus_revision() != revision

    # A second sync at the same (now-current) dating version is a no-op.
    revision_2 = ledger.corpus_revision()
    versions_after_bump = {unit_id: ledger._last_version(unit_id) for unit_id in versions_before}
    sources.sync([SOURCES], ledger, cache_db=cache_db)
    assert {unit_id: ledger._last_version(unit_id) for unit_id in versions_before} == versions_after_bump
    assert ledger.corpus_revision() == revision_2


# -- issue #45: first-run behaviour treats the current constant as applied --


def test_a_fresh_or_pre_upgrade_ledger_versions_nothing_extra_on_its_first_sync(tmp_path):
    from strata import dating

    ledger = Ledger(tmp_path / "ledger.db")
    cache_db = tmp_path / "store.db"
    assert ledger.dating_version() is None  # a fresh ledger has never recorded one

    report = sources.sync([SOURCES], ledger, cache_db=cache_db)
    versions_before = {r.ref: ledger._last_version(r.ref) for r in report.records}
    assert all(n == 1 for n in versions_before.values())
    assert ledger.dating_version() == dating.DATING_VERSION

    # A ledger "from before this change" is a real file whose corpus_revision
    # table has no dating_version column at all (the schema before #45).
    # Rebuilt the SQLite way - a copy of the table without the column, then
    # a swap - so the file is exactly what an older commit left behind.
    revision = ledger.corpus_revision()
    ledger.close()
    _downgrade_corpus_revision_table(tmp_path / "ledger.db")

    ledger = Ledger(tmp_path / "ledger.db")  # must open, not raise
    assert ledger.dating_version() is None
    assert ledger.corpus_revision() == revision

    sources.sync([SOURCES], ledger, cache_db=cache_db)
    for ref, before in versions_before.items():
        assert ledger._last_version(ref) == before  # no version bump from the upgrade alone
    assert ledger.corpus_revision() == revision  # no revision advance either
    assert ledger.dating_version() == dating.DATING_VERSION


def _downgrade_corpus_revision_table(path: Path) -> None:
    """Rewrite ``corpus_revision`` as the pre-#45 schema (``id``, ``token``
    only), keeping its token."""
    import sqlite3

    conn = sqlite3.connect(str(path))
    with conn:
        conn.executescript(
            """
            CREATE TABLE corpus_revision_old (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                token INTEGER NOT NULL
            );
            INSERT INTO corpus_revision_old (id, token) SELECT id, token FROM corpus_revision;
            DROP TABLE corpus_revision;
            ALTER TABLE corpus_revision_old RENAME TO corpus_revision;
            """
        )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(corpus_revision)")}
    conn.close()
    assert columns == {"id", "token"}


# -- issue #54: a converter bump sweeps its own stale generation ------------


def _cache_rows_by_converter(cache_db: Path) -> dict[str, int]:
    import sqlite3

    conn = sqlite3.connect(str(cache_db))
    rows = conn.execute("SELECT converter, COUNT(*) FROM conversions GROUP BY converter").fetchall()
    conn.close()
    return dict(rows)


def _seed_bare_converter_row(cache_db: Path, converter: str) -> None:
    """A row under a pre-#45 bare converter name (no ``@version``), as a
    real cache written before that change would hold."""
    import json
    import sqlite3

    conn = sqlite3.connect(str(cache_db))
    with conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS conversions ("
            "sha256 TEXT NOT NULL, converter TEXT NOT NULL, "
            "paragraphs TEXT, title TEXT, skip_reason TEXT, "
            "PRIMARY KEY (sha256, converter))"
        )
        conn.execute(
            "INSERT INTO conversions (sha256, converter, paragraphs, title, skip_reason) "
            "VALUES (?, ?, ?, ?, NULL)",
            ("stale-sha", converter, json.dumps(["a stale paragraph"]), "a stale title"),
        )
    conn.close()


def test_bumping_a_converter_sweeps_only_its_own_stale_generation(tmp_path, monkeypatch):
    ledger = Ledger(tmp_path / "ledger.db")
    cache_db = tmp_path / "store.db"
    sources.sync([SOURCES], ledger, cache_db=cache_db)

    before = _cache_rows_by_converter(cache_db)
    text_v1 = f"text@{sources.normalizer.CONVERTER_VERSIONS['text']}"
    assert before[text_v1] > 0
    others_before = {converter: count for converter, count in before.items() if converter != text_v1}

    monkeypatch.setitem(
        sources.normalizer.CONVERTER_VERSIONS, "text", sources.normalizer.CONVERTER_VERSIONS["text"] + 1
    )
    sources.sync([SOURCES], ledger, cache_db=cache_db)

    after = _cache_rows_by_converter(cache_db)
    text_v2 = f"text@{sources.normalizer.CONVERTER_VERSIONS['text']}"
    assert text_v1 not in after  # every stale text@1 row is gone
    assert after[text_v2] == before[text_v1]  # the new generation replaces it row for row
    for converter, count in others_before.items():
        assert after[converter] == count  # converters that did not bump keep every row


def test_a_bare_pre_issue_45_row_is_swept_on_the_first_sync_that_versions_its_converter(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    cache_db = tmp_path / "store.db"
    _seed_bare_converter_row(cache_db, "text")

    sources.sync([SOURCES], ledger, cache_db=cache_db)

    after = _cache_rows_by_converter(cache_db)
    assert "text" not in after  # the bare pre-#45 key is gone
    assert after[f"text@{sources.normalizer.CONVERTER_VERSIONS['text']}"] > 0


def test_a_second_sync_at_the_same_versions_sweeps_nothing(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    cache_db = tmp_path / "store.db"
    sources.sync([SOURCES], ledger, cache_db=cache_db)
    before = _cache_rows_by_converter(cache_db)

    sources.sync([SOURCES], ledger, cache_db=cache_db)

    assert _cache_rows_by_converter(cache_db) == before

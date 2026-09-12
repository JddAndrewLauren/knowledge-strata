"""The ledger (ADR-0001, wayfinder #6, #15, #10): exact-text anchor alignment,
durable current/retired text, document-order ranges and the corpus revision.

Every scenario is hermetic: a temporary sqlite file, paragraph lists written
inline or borrowed from ``scripts/make_fixtures.py``. No network, no corpus.
"""

import sys
from pathlib import Path

import pytest

from strata.ledger import AlignResult, Ledger, _match_paragraphs

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import make_fixtures  # noqa: E402


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    yield led
    led.close()


# -- alignment: acceptance #22 test 6, via scripts/make_fixtures.py --------


def test_paragraph_versions_align_as_the_fixture_describes(ledger):
    versions = make_fixtures.paragraph_versions()
    ledger.register(["sources/journal.txt"])

    v1 = ledger.align("sources/journal.txt", "hash-v1", versions["v1"], converter="text", at="2001-06-01")
    assert v1.changed
    assert v1.version == 1
    assert (v1.kept, v1.retired, v1.added) == (0, 0, 7)
    assert v1.anchors == (1, 2, 3, 4, 5, 6, 7)
    assert v1.line() == "SRC-000001: v1, 0 kept, 0 retired, 7 added"

    v2 = ledger.align("sources/journal.txt", "hash-v2", versions["v2"], converter="text", at="2001-06-02")
    assert v2.changed
    assert v2.version == 2
    # p1 kept, p3/p4 duplicates pair, p6 moved-but-kept => 4 kept.
    # p2 cosmetic edit, p5 deleted, p7 NFC/NFD change => 3 retired.
    # the punctuation-dropped line, the NFD line and the new paragraph => 3 added.
    assert (v2.kept, v2.retired, v2.added) == (4, 3, 3)
    assert v2.anchors == (6, 1, 8, 3, 9, 4, 10)
    assert v2.line() == "SRC-000001: v2, 4 kept, 3 retired, 3 added"

    # Kept anchors read back their (identical) live text unmarked.
    assert ledger.text("SRC-000001 p1") == "The tie went out at ten past eleven at night."
    assert ledger.text("SRC-000001 p3") == "Nobody schedules across it."
    assert ledger.text("SRC-000001 p4") == "Nobody schedules across it."
    assert ledger.text("SRC-000001 p6") == "Derate to forty percent."

    # Retired anchors keep their exact original text, never the new wording.
    for p, original in [
        (2, "Tomas held the noon submission."),
        (5, "Priya called Marcus at home."),
        (7, "Café on the corner."),
    ]:
        text = ledger.text(f"SRC-000001 p{p}")
        assert "retired at v2" in text
        assert "the v1 text it cited" in text
        assert original in text
        assert text.endswith("The record's current text is SRC-000001.")

    # Fresh anchors carry the new wording, not the old.
    assert ledger.text("SRC-000001 p8") == "Tomas held the noon submission"
    assert ledger.text("SRC-000001 p9") == versions["v2"][4]  # NFD, distinct str from p7's NFC
    assert ledger.text("SRC-000001 p9") != ledger.text("SRC-000001 p7").split("\n")[1]
    assert ledger.text("SRC-000001 p10") == "A new closing paragraph."


def test_no_retired_number_ever_reappears_across_ten_further_versions(ledger):
    ledger.register(["sources/churn.txt"])
    paragraphs = ["alpha", "beta", "gamma"]
    live = set(ledger.align("sources/churn.txt", "h0", paragraphs, converter="text", at="d0").anchors)
    retired_ever: set[int] = set()

    for i in range(10):
        # Retire one paragraph, add a new one, each version.
        paragraphs = paragraphs[1:] + [f"new-{i}"]
        result = ledger.align("sources/churn.txt", f"h{i + 1}", paragraphs, converter="text", at=f"d{i + 1}")
        current = set(result.anchors)
        retired_ever |= live - current
        assert not (current & retired_ever), "a retired number reappeared as live"
        live = current


# -- reading anchors: acceptance #22 test 2 ---------------------------------


def test_reading_a_missing_or_whole_record_ref_is_refused(ledger):
    ledger.register(["sources/x.txt"])
    ledger.align("sources/x.txt", "h", ["one", "two"], converter="text", at="d")
    with pytest.raises(ValueError):
        ledger.text("SRC-000001")  # whole record, not a paragraph
    with pytest.raises(ValueError):
        ledger.text("SRC-000001 p99")  # never existed - message says so
    with pytest.raises(ValueError, match="no such anchor"):
        ledger.text("SRC-000001 p99")


def test_retired_anchor_still_readable_after_the_unit_is_deleted_and_a_further_version(ledger):
    """Readback survives cache deletion and raw-file changes or removal in
    spirit: the ledger persists retired text with no dependency on the raw
    file or a cache, so later ledger activity cannot lose it."""
    ledger.register(["sources/x.txt"])
    ledger.align("sources/x.txt", "h1", ["kept", "gone"], converter="text", at="d1")
    ledger.align("sources/x.txt", "h2", ["kept"], converter="text", at="d2")  # "gone" retires
    retired = ledger.text("SRC-000001 p2")
    assert "gone" in retired

    ledger.register(["sources/y.txt"])
    ledger.align("sources/y.txt", "h3", ["unrelated"], converter="text", at="d3")
    ledger.retire_unit("sources/x.txt", at="d4")  # the raw file is removed entirely

    still_retired = ledger.text("SRC-000001 p2")
    assert "gone" in still_retired
    # The other, never-changed anchor is retired too, by the deletion.
    now_retired = ledger.text("SRC-000001 p1")
    assert "retired at v3" in now_retired
    assert "kept" in now_retired


# -- atomicity: acceptance #22 test 3 ---------------------------------------


def test_an_interrupted_alignment_leaves_the_previous_version_intact(ledger, monkeypatch):
    ledger.register(["sources/x.txt"])
    ledger.align("sources/x.txt", "h1", ["one", "two"], converter="text", at="d1")

    def boom(self, anchor_id, text):
        raise RuntimeError("simulated crash between anchor update and text write")

    monkeypatch.setattr(Ledger, "_write_retired_text", boom)
    with pytest.raises(RuntimeError):
        ledger.align("sources/x.txt", "h2", ["one"], converter="text", at="d2")  # "two" would retire

    monkeypatch.undo()
    # The old version is untouched: both anchors are still live with v1 text.
    assert ledger.text("SRC-000001 p1") == "one"
    assert ledger.text("SRC-000001 p2") == "two"
    assert ledger._last_version("SRC-000001") == 1


# -- ranges: acceptance #22 test 4 ------------------------------------------


def test_ranges_traverse_document_order_not_numeric_order(ledger):
    ledger.register(["sources/journal.txt"])
    versions = make_fixtures.paragraph_versions()
    ledger.align("sources/journal.txt", "hash-v1", versions["v1"], converter="text", at="2001-06-01")
    ledger.align("sources/journal.txt", "hash-v2", versions["v2"], converter="text", at="2001-06-02")
    # v2 document order is (6, 1, 8, 3, 9, 4, 10): non-monotonic, non-contiguous.

    forward = ledger.range("SRC-000001 p6-4")  # p6 before p4 in document order
    assert [p for p, _ in forward] == [6, 1, 8, 3, 9, 4]

    tail = ledger.range("SRC-000001 p6-")
    assert [p for p, _ in tail] == [6, 1, 8, 3, 9, 4, 10]

    with pytest.raises(ValueError, match="reversed"):
        ledger.range("SRC-000001 p4-6")  # p4 comes after p6 in document order

    retired_endpoint = ledger.range("SRC-000001 p2-4")
    assert isinstance(retired_endpoint, str)
    assert "retired" in retired_endpoint

    missing_endpoint = ledger.range("SRC-000001 p999-4")
    assert isinstance(missing_endpoint, str)
    assert "does not exist" in missing_endpoint

    # The single retired anchor named above is still readable directly.
    assert "Tomas held the noon submission." in ledger.text("SRC-000001 p2")


def test_range_requires_a_span_not_a_single_anchor(ledger):
    ledger.register(["sources/x.txt"])
    ledger.align("sources/x.txt", "h", ["one"], converter="text", at="d")
    with pytest.raises(ValueError):
        ledger.range("SRC-000001 p1")


# -- ids: acceptance #22 test 5 ----------------------------------------------


def test_new_units_are_numbered_in_sorted_path_order_on_first_sight(ledger):
    ids = ledger.register(["b.txt", "a.txt", "c.txt"])
    assert ids["a.txt"] < ids["b.txt"] < ids["c.txt"]
    assert ids["a.txt"] == "SRC-000001"


def test_merged_corpora_and_deletion_never_reuse_an_id_or_choke_on_a_gap(ledger):
    ledger.register(["a.txt", "b.txt"])
    # Simulate a merged corpus that already carries a much higher number.
    ledger._conn.execute("INSERT INTO units (id, path, sha256, deleted) VALUES ('SRC-000100', 'merged.txt', NULL, 0)")
    ledger._conn.commit()

    ledger.align("a.txt", "h", ["x"], converter="text", at="d")
    ledger.retire_unit("a.txt", at="d")  # deleted; its row and id are never removed

    ids = ledger.register(["new.txt"])
    assert ids["new.txt"] == "SRC-000101"  # max(id) + 1, no reuse, no crash on the gap


def test_register_is_idempotent(ledger):
    first = ledger.register(["a.txt", "b.txt"])
    second = ledger.register(["b.txt", "a.txt", "c.txt"])
    assert second["a.txt"] == first["a.txt"]
    assert second["b.txt"] == first["b.txt"]


def test_align_refuses_an_unregistered_path(ledger):
    with pytest.raises(KeyError):
        ledger.align("never/registered.txt", "h", ["x"], converter="text", at="d")


# -- corpus revision: acceptance #22 test 5 / #22 test 6 --------------------


def test_corpus_revision_advances_on_content_deletion_converter_and_dating_change(ledger):
    ledger.register(["a.txt"])
    start = ledger.corpus_revision()

    ledger.align("a.txt", "h1", ["x"], converter="text", at="d1")
    after_new_unit = ledger.corpus_revision()
    assert after_new_unit != start

    unchanged = ledger.align("a.txt", "h1", ["x"], converter="text", at="d1")
    assert not unchanged.changed
    after_unchanged = ledger.corpus_revision()
    assert after_unchanged == after_new_unit  # a cache-only rebuild: no content change

    dated = ledger.align("a.txt", "h1", ["x"], converter="text", at="d1", dated=True)
    assert dated.changed
    after_dated = ledger.corpus_revision()
    assert after_dated != after_unchanged  # dating changed even though content did not

    converted = ledger.align("a.txt", "h1", ["x"], converter="text-v2", at="d2")
    assert converted.changed
    after_converter = ledger.corpus_revision()
    assert after_converter != after_dated

    ledger.retire_unit("a.txt", at="d3")
    after_delete = ledger.corpus_revision()
    assert after_delete != after_converter


# -- the alignment helper in isolation --------------------------------------


def test_match_paragraphs_pairs_equal_duplicates_in_document_order():
    old = [(1, "a"), (2, "dup"), (3, "dup"), (4, "b")]
    assignment, retiring = _match_paragraphs(old, ["dup", "z", "dup"])
    assert assignment == [2, None, 3]
    assert retiring == [1, 4]


def test_state_persists_across_reopening_the_same_file(tmp_path):
    """The ledger is a file, not a connection: closing and reopening it must
    not lose ids, anchors, retired text or the corpus revision."""
    db = tmp_path / "ledger.db"
    first = Ledger(db)
    first.register(["sources/x.txt"])
    first.align("sources/x.txt", "h1", ["kept", "gone"], converter="text", at="d1")
    first.align("sources/x.txt", "h2", ["kept"], converter="text", at="d2")
    revision = first.corpus_revision()
    first.close()

    reopened = Ledger(db)
    try:
        assert reopened.register(["sources/x.txt"]) == {"sources/x.txt": "SRC-000001"}
        assert reopened.text("SRC-000001 p1") == "kept"
        assert "gone" in reopened.text("SRC-000001 p2")
        assert reopened.corpus_revision() == revision
    finally:
        reopened.close()


def test_match_paragraphs_requires_byte_exact_equality():
    old = [(1, "Café")]  # NFC
    assignment, retiring = _match_paragraphs(old, ["Café"])  # NFD: visually identical, not equal
    assert assignment == [None]
    assert retiring == [1]

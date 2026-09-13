"""Coverage: eligible-digest credit, conservative suppression, freshness
(CONTEXT.md "Coverage"; acceptance #22 test 5).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from strata.embeddings import FakeEmbedder
from strata.index import Index
from strata.ledger import Ledger
from strata.record import first_sentence

from factories import exact, inferred_month, make_manuscript, make_note, make_source, unknown

WEST_DESK_DIGESTS = Path(__file__).resolve().parent.parent / "examples" / "west-desk" / "notes" / "digest"


def _digest_frontmatter(path: Path) -> dict:
    """A minimal, test-only frontmatter reader for the committed west-desk
    digests: no general notes adapter exists yet (that is a separate,
    unbuilt issue), and this fixture's frontmatter is two flat keys plus one
    one-level-nested ``window``."""
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    _, _, rest = text.partition("---\n")
    block, _, _ = rest.partition("\n---\n")
    data: dict = {}
    window: dict = {}
    for line in block.splitlines():
        if line.startswith("  ") and ":" in line:
            key, _, value = line.strip().partition(":")
            window[key.strip()] = value.strip()
        elif ":" in line:
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            if value:
                data[key] = value
    if window:
        data["window"] = window
    return data


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    yield led
    led.close()


def index_of(tmp_path, ledger, chunk_tokens=80_000):
    return Index(tmp_path / "index.db", ledger=ledger, embedder=FakeEmbedder(), chunk_tokens=chunk_tokens, semantic=False)


def digest(ledger, path, window, *, complete=True, revision=None):
    rev = revision if revision is not None else str(ledger.corpus_revision())
    return make_note(
        path,
        ["Digest body."],
        type="digest",
        window=window,
        corpus_revision=rev,
        coverage_complete=complete,
    )


def _assigned_refs(index, reply):
    """The bare refs every assignment of ``reply`` executes to."""
    refs = set()
    chunks = list(reply.chunks)
    cursor = reply.continuation
    while cursor:
        page = index.search(cursor=cursor)
        chunks.extend(page.chunks)
        cursor = page.continuation
    for chunk in chunks:
        cursor = chunk.cursor
        while cursor:
            page = index.search(cursor=cursor)
            refs.update(hit.ref.split(" ")[0] for hit in page.hits)
            cursor = page.continuation
    return refs


JUNE = ("2001-06-01", "2001-06-30")
DIGEST = "notes/digest/2001-06-01--2001-06-30.md"


def _covered_june(index):
    reply = index.search(from_=JUNE[0], to=JUNE[1])
    return [row.ref for row in reply.covered], {h.ref for h in reply.hits}


@pytest.fixture
def completed_window(tmp_path, ledger):
    """A source-wide June reading completed and written up: two June
    sources, one July source, and an eligible digest at the resulting
    corpus revision. Yields (index, sources by name, digest)."""
    index = index_of(tmp_path, ledger)
    sources = {
        "a": make_source(ledger, "a.txt", ["June text A."], date=exact("2001-06-01")),
        "b": make_source(ledger, "b.txt", ["June text B."], date=exact("2001-06-15")),
        "july": make_source(ledger, "july.txt", ["July text."], date=exact("2001-07-03")),
    }
    index.sync(list(sources.values()))
    d = digest(ledger, DIGEST, JUNE)
    index.sync([*sources.values(), d])
    covered, _hits = _covered_june(index)
    assert covered == [d.ref]
    yield index, sources, d
    index.close()


def test_an_eligible_digest_is_reported_covered_and_suppresses_its_chunk(tmp_path, ledger):
    index = index_of(tmp_path, ledger, chunk_tokens=10)
    src = make_source(ledger, "a.txt", ["Some content here about the meeting."], date=exact("2001-06-01"))
    index.sync([src])
    d = digest(ledger, DIGEST, JUNE)
    index.sync([src, d])

    # A source-wide reading, per CONTEXT.md's coverage definition (kind source).
    reply = index.search(from_="2001-06-01", to="2001-06-30", kind="source")
    assert [row.ref for row in reply.covered] == [d.ref]
    assert reply.chunks == []  # the only source day is inside the eligible window


def test_a_source_change_inside_the_window_drops_credit_while_the_digest_stays_searchable(completed_window):
    index, sources, d = completed_window
    changed = make_source(index._ledger, "a.txt", ["June text A, changed."], date=exact("2001-06-01"))
    index.sync([changed, sources["b"], sources["july"], d])
    covered, hits = _covered_june(index)
    assert covered == [] and d.ref in hits


def test_a_source_change_outside_the_window_drops_credit_conservatively(completed_window):
    index, sources, d = completed_window
    changed = make_source(index._ledger, "july.txt", ["July text, changed."], date=exact("2001-07-03"))
    index.sync([sources["a"], sources["b"], changed, d])
    covered, hits = _covered_june(index)
    assert covered == [] and d.ref in hits


def test_an_added_source_drops_credit(completed_window):
    index, sources, d = completed_window
    added = make_source(index._ledger, "new.txt", ["Newly arrived June text."], date=exact("2001-06-20"))
    index.sync([*sources.values(), added, d])
    covered, hits = _covered_june(index)
    assert covered == [] and d.ref in hits


def test_a_removed_source_drops_credit(completed_window):
    index, sources, d = completed_window
    index._ledger.retire_unit("b.txt")
    index.sync([sources["a"], sources["july"], d])
    covered, hits = _covered_june(index)
    assert covered == [] and d.ref in hits


def test_a_conversion_change_drops_credit(completed_window):
    index, sources, d = completed_window
    reconverted = make_source(index._ledger, "a.txt", ["June text A."], date=exact("2001-06-01"), converter="pandoc")
    index.sync([reconverted, sources["b"], sources["july"], d])
    covered, hits = _covered_june(index)
    assert covered == [] and d.ref in hits


def test_a_dating_change_drops_credit(completed_window):
    index, sources, d = completed_window
    redated = make_source(index._ledger, "a.txt", ["June text A."], date=exact("2001-06-02"), dated=True)
    index.sync([redated, sources["b"], sources["july"], d])
    covered, hits = _covered_june(index)
    assert covered == [] and d.ref in hits


def test_a_note_edit_alone_preserves_credit(completed_window):
    index, sources, d = completed_window
    theme = make_note("notes/theme/cutoff.md", ["First draft."], type="theme")
    index.sync([*sources.values(), d, theme])
    revision_before = index.index_revision
    edited = make_note("notes/theme/cutoff.md", ["Second draft."], type="theme")
    index.sync([*sources.values(), d, edited])
    assert index.index_revision != revision_before  # the index moved ...
    covered, _hits = _covered_june(index)
    assert covered == [d.ref]  # ... the corpus revision, and the credit, did not


def test_note_edits_and_cache_only_rebuilds_preserve_coverage(tmp_path, ledger):
    src = make_source(ledger, "a.txt", ["Content."], date=exact("2001-06-01"))
    idx1 = index_of(tmp_path, ledger)
    idx1.sync([src])
    d = digest(ledger, DIGEST, JUNE)
    idx1.sync([src, d])
    assert idx1.search(from_="2001-06-01", to="2001-06-30").covered
    idx1.close()

    # cache-only rebuild: same records, fresh index.db
    (tmp_path / "index.db").unlink()
    idx2 = index_of(tmp_path, ledger)
    idx2.sync([src, d])
    assert idx2.search(from_="2001-06-01", to="2001-06-30").covered
    idx2.close()


def test_legacy_malformed_filtered_split_and_incomplete_digests_grant_no_credit(tmp_path, ledger):
    index = index_of(tmp_path, ledger)
    src = make_source(ledger, "a.txt", ["Content."], date=exact("2001-06-01"))
    index.sync([src])
    current = str(ledger.corpus_revision())

    incomplete = digest(ledger, "notes/digest/2001-06-incomplete.md", JUNE, complete=False)
    stale = digest(ledger, "notes/digest/2001-06-stale.md", JUNE, revision="not-current")
    legacy = make_note(
        "notes/digest/2001-06-legacy.md", ["Legacy digest, no window or revision."], type="digest", coverage_complete=True
    )
    # The notes adapter rejects a malformed window (#29): the Record arrives
    # with no window and a warning, whatever else its frontmatter claims.
    malformed = make_note(
        "notes/digest/2001-06-malformed.md",
        ["Digest with a malformed window."],
        type="digest",
        corpus_revision=current,
        coverage_complete=True,
        warnings=("window: not a from/to pair",),
    )
    # A filtered reading (a query or who restriction) is not source-wide,
    # so the adapter never marks it complete.
    filtered = make_note(
        "notes/digest/2001-06-cutoff.md",
        ["Digest of the cutoff search only."],
        type="digest",
        window=JUNE,
        corpus_revision=current,
        coverage_complete=False,
    )
    # One split assignment's digest carries no window of its own.
    split = make_note(
        "notes/digest/2001-06-01-part-2.md", ["Second split of June 1."], type="digest", corpus_revision=current
    )
    digests = [incomplete, stale, legacy, malformed, filtered, split]
    index.sync([src, *digests])

    reply = index.search(from_="2001-06-01", to="2001-06-30")
    assert reply.covered == []
    # every one of them remains an ordinary, searchable hit
    refs = {h.ref for h in reply.hits}
    assert {d.ref for d in digests} <= refs
    assert src.ref in _assigned_refs(index, reply)  # and nothing is suppressed


def test_unknown_and_coarse_dated_records_are_never_suppressed_by_overlap(tmp_path, ledger):
    index = index_of(tmp_path, ledger, chunk_tokens=10)
    coarse = make_source(ledger, "m.txt", ["Coarse dated content."], date=inferred_month("2001-06-01", "folder"))
    undated = make_source(ledger, "u.txt", ["Undated content."], date=unknown())
    index.sync([coarse, undated])
    d = digest(ledger, DIGEST, JUNE)
    index.sync([coarse, undated, d])

    reply = index.search(from_="2001-06-01", to="2001-06-30")
    assert reply.covered
    # both the coarse-dated and the undated record still need assignments
    assigned = _assigned_refs(index, reply)
    assert coarse.ref in assigned
    assert undated.ref in assigned


def test_only_source_records_are_suppressed_by_an_eligible_window(tmp_path, ledger):
    """A digest covers source reading; an exact-dated manuscript chapter
    inside the window is still assigned, only the source is not."""
    index = index_of(tmp_path, ledger, chunk_tokens=10)
    src = make_source(ledger, "a.txt", ["Source content."], date=exact("2001-06-15"))
    chapter = make_manuscript("manuscript/ch05.md", ["# June", "Chapter text."], date=exact("2001-06-15"), title="June")
    index.sync([src, chapter])
    d = digest(ledger, DIGEST, JUNE)
    index.sync([src, chapter, d])

    reply = index.search(from_="2001-06-01", to="2001-06-30")
    assert [row.ref for row in reply.covered] == [d.ref]
    assigned = _assigned_refs(index, reply)
    assert chapter.ref in assigned
    assert src.ref not in assigned


def test_the_west_desk_demo_digests_show_eligible_covered_and_stale_ones_do_not(tmp_path, ledger):
    """The committed demo digests (examples/west-desk/notes/digest/): two
    carry the ``SET-BY-TEST-AFTER-SYNC`` placeholder this test rewrites with
    the ledger's real revision before asserting; a stale, a legacy (no
    revision) and a split/interrupted (no window) digest must grant no
    credit even though all five stay searchable."""
    index = Index(tmp_path / "index.db", ledger=ledger, embedder=FakeEmbedder(), semantic=False)
    current_revision = str(ledger.corpus_revision())

    records = []
    for path in sorted(WEST_DESK_DIGESTS.glob("*.md")):
        meta = _digest_frontmatter(path)
        paragraphs = tuple(path.read_text(encoding="utf-8").split("\n\n"))
        revision = meta.get("corpus_revision")
        if revision == "SET-BY-TEST-AFTER-SYNC":
            revision = current_revision
        window = meta.get("window")
        records.append(
            make_note(
                f"notes/digest/{path.name}",
                paragraphs,
                type="digest",
                window=(window["from"], window["to"]) if window else None,
                corpus_revision=revision,
                coverage_complete=(meta.get("coverage_complete") == "true") or None,
                title=first_sentence(paragraphs) or path.stem,
            )
        )
    index.sync(records)

    reply = index.search(from_="2001-04-01", to="2001-07-16")
    covered_refs = {row.ref for row in reply.covered}
    eligible = {"notes/digest/2001-04-01--2001-04-30.md", "notes/digest/2001-05-01--2001-05-31.md"}
    ineligible = {
        "notes/digest/2001-05-01--2001-05-31-2.md",  # stale revision
        "notes/digest/2001-06-01--2001-06-30.md",  # legacy, no revision
        "notes/digest/2001-07-01--2001-07-16.md",  # split/incomplete, no window
    }
    assert eligible <= covered_refs
    assert ineligible.isdisjoint(covered_refs)

    # all five stay searchable regardless of eligibility
    hit_refs = {h.ref for h in index.search(from_="2001-04-01", to="2001-07-16").hits}
    assert (eligible | ineligible) <= hit_refs
    index.close()

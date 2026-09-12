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

from factories import exact, inferred_month, make_note, make_source, unknown

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


def test_an_eligible_digest_is_reported_covered_and_suppresses_its_chunk(tmp_path, ledger):
    index = index_of(tmp_path, ledger, chunk_tokens=10)
    src = make_source(ledger, "a.txt", ["Some content here about the meeting."], date=exact("2001-06-01"))
    index.sync([src])
    d = digest(ledger, "notes/digest/2001-06-01--2001-06-30.md", ("2001-06-01", "2001-06-30"))
    index.sync([src, d])

    # A source-wide reading, per CONTEXT.md's coverage definition (kind source).
    reply = index.search(from_="2001-06-01", to="2001-06-30", kind="source")
    assert [row.ref for row in reply.covered] == [d.ref]
    assert reply.chunks == []  # the only source day is inside the eligible window


def test_a_source_change_inside_the_window_drops_coverage(tmp_path, ledger):
    index = index_of(tmp_path, ledger)
    src = make_source(ledger, "a.txt", ["Original text."], date=exact("2001-06-01"))
    index.sync([src])
    d = digest(ledger, "notes/digest/2001-06-01--2001-06-30.md", ("2001-06-01", "2001-06-30"))
    index.sync([src, d])
    stale_revision = ledger.corpus_revision()
    assert index.search(from_="2001-06-01", to="2001-06-30").covered

    changed = make_source(ledger, "a.txt", ["Changed text."], date=exact("2001-06-01"))
    d_stale = digest(
        ledger, "notes/digest/2001-06-01--2001-06-30.md", ("2001-06-01", "2001-06-30"), revision=stale_revision
    )
    index.sync([changed, d_stale])
    assert index.search(from_="2001-06-01", to="2001-06-30").covered == []


def test_note_edits_and_cache_only_rebuilds_preserve_coverage(tmp_path, ledger):
    src = make_source(ledger, "a.txt", ["Content."], date=exact("2001-06-01"))
    idx1 = index_of(tmp_path, ledger)
    idx1.sync([src])
    d = digest(ledger, "notes/digest/2001-06-01--2001-06-30.md", ("2001-06-01", "2001-06-30"))
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

    incomplete = digest(ledger, "notes/digest/2001-06-incomplete.md", ("2001-06-01", "2001-06-30"), complete=False)
    stale = digest(ledger, "notes/digest/2001-06-stale.md", ("2001-06-01", "2001-06-30"), revision="not-current")
    no_window = make_note(
        "notes/digest/2001-06-legacy.md", ["Legacy digest, no window."], type="digest", coverage_complete=True
    )
    index.sync([src, incomplete, stale, no_window])

    reply = index.search(from_="2001-06-01", to="2001-06-30")
    assert reply.covered == []
    # legacy/stale/incomplete digests remain ordinary, searchable hits
    refs = {h.ref for h in reply.hits}
    assert incomplete.ref in refs and stale.ref in refs and no_window.ref in refs


def test_unknown_and_coarse_dated_records_are_never_suppressed_by_overlap(tmp_path, ledger):
    index = index_of(tmp_path, ledger, chunk_tokens=10)
    coarse = make_source(ledger, "m.txt", ["Coarse dated content."], date=inferred_month("2001-06-01", "folder"))
    undated = make_source(ledger, "u.txt", ["Undated content."], date=unknown())
    index.sync([coarse, undated])
    d = digest(ledger, "notes/digest/2001-06-01--2001-06-30.md", ("2001-06-01", "2001-06-30"))
    index.sync([coarse, undated, d])

    reply = index.search(from_="2001-06-01", to="2001-06-30")
    assert reply.covered
    # both the coarse-dated and the undated record still need assignments
    assigned_refs = set()
    for chunk in reply.chunks:
        import base64
        import json

        padded = chunk.cursor + "=" * (-len(chunk.cursor) % 4)
        state = json.loads(base64.urlsafe_b64decode(padded))
        assigned_refs.update(state["scope"])
    assert coarse.ref in assigned_refs
    assert undated.ref in assigned_refs


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

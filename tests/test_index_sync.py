"""sync: freshness, per-paragraph embedding cache, revisions (acceptance #22
test 1, and CONTEXT.md "Index revision" / "Corpus revision").
"""

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
    idx = Index(tmp_path / "index.db", ledger=ledger, embedder=FakeEmbedder())
    yield idx
    idx.close()


def test_sync_inserts_and_embeds_every_new_paragraph(index):
    embedder = index._embedder
    r1 = make_source(index._ledger, "a.txt", ["Alpha one.", "Alpha two."], date=exact("2001-06-01"))
    result = index.sync([r1])
    assert result == {"changed": True, "records": 1}
    assert embedder.passage_calls == ["Alpha one.", "Alpha two."]


def test_unchanged_record_is_not_reembedded(index):
    embedder = index._embedder
    r1 = make_source(index._ledger, "a.txt", ["Alpha one.", "Alpha two."], date=exact("2001-06-01"))
    index.sync([r1])
    embedder.passage_calls.clear()
    index.sync([r1])
    assert embedder.passage_calls == []


def test_changed_record_only_reembeds_the_changed_paragraph(index):
    embedder = index._embedder
    ledger = index._ledger
    r1 = make_source(ledger, "a.txt", ["Alpha one.", "Alpha two."], date=exact("2001-06-01"))
    index.sync([r1])
    embedder.passage_calls.clear()
    r2 = make_source(ledger, "a.txt", ["Alpha one.", "Alpha two, edited."], date=exact("2001-06-01"))
    index.sync([r2])
    assert embedder.passage_calls == ["Alpha two, edited."]


def test_a_paragraph_reused_verbatim_elsewhere_is_not_reembedded(index):
    embedder = index._embedder
    ledger = index._ledger
    shared = "Nobody schedules across it."
    r1 = make_source(ledger, "a.txt", [shared], date=exact("2001-06-01"))
    r2 = make_source(ledger, "b.txt", ["Something else entirely."], date=exact("2001-06-02"))
    index.sync([r1, r2])
    embedder.passage_calls.clear()
    r3 = make_source(ledger, "c.txt", [shared], date=exact("2001-06-03"))
    index.sync([r1, r2, r3])
    assert embedder.passage_calls == []


def test_a_deleted_records_text_no_longer_matches_a_lexical_search(index):
    ledger = index._ledger
    doomed = make_source(ledger, "a.txt", ["A distinctive unmatched phrase."], date=exact("2001-06-01"))
    kept = make_source(ledger, "b.txt", ["Something else."], date=exact("2001-06-02"))
    index.sync([doomed, kept])
    index.sync([kept])
    reply = index.search("distinctive")
    assert reply.lexical_records == 0
    assert doomed.ref not in {h.ref.split(" ")[0] for h in reply.hits}


def test_vanished_record_is_deleted(index):
    ledger = index._ledger
    r1 = make_source(ledger, "a.txt", ["Alpha."], date=exact("2001-06-01"))
    r2 = make_source(ledger, "b.txt", ["Beta."], date=exact("2001-06-02"))
    index.sync([r1, r2])
    index.sync([r1])
    row = index._conn.execute("SELECT COUNT(*) AS n FROM records WHERE ref = ?", (r2.ref,)).fetchone()
    assert row["n"] == 0
    prow = index._conn.execute("SELECT COUNT(*) AS n FROM paragraphs WHERE ref = ?", (r2.ref,)).fetchone()
    assert prow["n"] == 0


def test_per_record_token_estimate_matches_ceil_bytes_over_4(index):
    ledger = index._ledger
    text = "Café déjà vu " * 50  # multibyte, so bytes != characters
    r1 = make_source(ledger, "a.txt", [text], date=exact("2001-06-01"))
    index.sync([r1])
    row = index._conn.execute("SELECT tokens FROM records WHERE ref = ?", (r1.ref,)).fetchone()
    import math

    expected = math.ceil(len(text.encode("utf-8")) / 4)
    assert row["tokens"] == expected


def test_index_revision_moves_on_a_note_edit_but_corpus_revision_does_not(index):
    ledger = index._ledger
    note1 = make_note("notes/theme/x.md", ["First."], type="theme")
    index.sync([note1])
    rev1 = index.index_revision
    corpus1 = ledger.corpus_revision()

    note2 = make_note("notes/theme/x.md", ["First, edited."], type="theme")
    index.sync([note2])
    assert index.index_revision != rev1
    assert ledger.corpus_revision() == corpus1


def test_a_source_change_moves_both_revisions(index):
    ledger = index._ledger
    r1 = make_source(ledger, "a.txt", ["Alpha."], date=exact("2001-06-01"))
    index.sync([r1])
    rev1 = index.index_revision
    corpus1 = ledger.corpus_revision()

    r2 = make_source(ledger, "a.txt", ["Alpha, edited."], date=exact("2001-06-01"))
    index.sync([r2])
    assert index.index_revision != rev1
    assert ledger.corpus_revision() != corpus1


def test_a_cache_rebuild_moves_index_revision_but_not_corpus_revision(tmp_path, ledger):
    r1 = make_source(ledger, "a.txt", ["Alpha."], date=exact("2001-06-01"))
    idx1 = Index(tmp_path / "index.db", ledger=ledger, embedder=FakeEmbedder())
    idx1.sync([r1])
    rev1 = idx1.index_revision
    corpus1 = ledger.corpus_revision()
    idx1.close()

    (tmp_path / "index.db").unlink()
    idx2 = Index(tmp_path / "index.db", ledger=ledger, embedder=FakeEmbedder())
    idx2.sync([r1])
    assert idx2.index_revision != rev1
    assert ledger.corpus_revision() == corpus1
    idx2.close()


def test_a_model_identity_change_reembeds_everything(tmp_path, ledger):
    r1 = make_source(ledger, "a.txt", ["Alpha.", "Beta."], date=exact("2001-06-01"))
    idx1 = Index(tmp_path / "index.db", ledger=ledger, embedder=FakeEmbedder())
    idx1.sync([r1])
    idx1.close()

    embedder2 = FakeEmbedder()
    embedder2.model_id = "fake-v2"
    idx2 = Index(tmp_path / "index.db", ledger=ledger, embedder=embedder2)
    idx2.sync([r1])
    assert sorted(embedder2.passage_calls) == ["Alpha.", "Beta."]
    idx2.close()

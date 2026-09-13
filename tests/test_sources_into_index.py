"""The sources adapter feeding the index end to end: west-desk ``sources/``
walked by :func:`strata.corpus.sources.sync` into a real ledger, its Records
synced into :class:`strata.index.Index`, then an edit and a delete carried
through both layers.

Hermetic: the corpus is a throwaway copy inside ``tmp_path`` (nothing is
written inside ``examples/``), the conversion cache is a temporary file, and
the embedder is the fake one.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from strata import dating, normalizer
from strata.corpus import sources
from strata.embeddings import FakeEmbedder
from strata.index import BadRef, Index
from strata.ledger import Ledger

from factories import make_note

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "examples" / "west-desk" / "sources"

JOURNAL_KEY = "000/2001/June/journal-outage-week.txt"
GLOSSARY_KEY = "000/memos/glossary.txt"
HELD = "Tomas held the noon submission. First time I had seen him hold anything."
EDITED = "Tomas held the noon submission, reluctantly."


def _source_units(corpus: Path) -> int:
    return sum(1 for p in corpus.rglob("*") if p.is_file())


def test_sources_sync_into_the_index_through_an_edit_and_a_delete(tmp_path):
    corpus = tmp_path / "sources"
    shutil.copytree(SOURCES, corpus)
    ledger = Ledger(tmp_path / "ledger.db")
    cache_db = tmp_path / "store.db"
    index = Index(tmp_path / "index.db", ledger=ledger, embedder=FakeEmbedder())
    try:
        # -- first sync: every convertible unit is one indexed record -------
        first = sources.sync([corpus], ledger, cache_db=cache_db)
        assert len(first.skipped) == 2
        assert len(first.records) == _source_units(corpus) - 2 == 22
        assert index.sync(list(first.records)) == {"changed": True, "records": 22}

        units = ledger.known_units()
        journal_id = units[JOURNAL_KEY][0]
        glossary_id = units[GLOSSARY_KEY][0]

        reply = index.search(query="noon submission")
        cited = [hit.ref for hit in reply.hits if hit.ref.startswith(f"{journal_id} ")]
        assert cited == [f"{journal_id} p3"]
        assert index.read(cited[0]).body == HELD
        assert index.read(glossary_id).labels[0].startswith(f"{glossary_id}  ")

        corpus_revision_1 = ledger.corpus_revision()
        index_revision_1 = index.index_revision
        assert reply.corpus_revision == corpus_revision_1
        # Hybrid search: the fake embedder's semantic side reaches every
        # record, so lexical evidence is what tells a word is absent.
        assert index.search(query="reluctantly").lexical_paragraphs == 0

        # -- edit one file: the index reflects the new text -----------------
        journal = corpus / "2001" / "June" / "journal-outage-week.txt"
        journal.write_text(journal.read_text(encoding="utf-8").replace(HELD, EDITED), encoding="utf-8")
        second = sources.sync([corpus], ledger, cache_db=cache_db)
        assert index.sync(list(second.records)) == {"changed": True, "records": 22}

        assert ledger.corpus_revision() != corpus_revision_1
        assert index.index_revision != index_revision_1
        edited = index.search(query="reluctantly")
        assert (edited.lexical_records, edited.lexical_paragraphs) == (1, 1)
        hits = [hit.ref for hit in edited.hits if hit.ref.startswith(f"{journal_id} ")]
        assert len(hits) == 1
        assert index.read(hits[0]).body == EDITED
        assert HELD not in index.read(journal_id).body

        corpus_revision_2 = ledger.corpus_revision()
        index_revision_2 = index.index_revision
        derate_records_before = index.search(query="Derate").lexical_records

        # -- delete one file: its unit retires and the record count drops ---
        (corpus / "memos" / "glossary.txt").unlink()
        third = sources.sync([corpus], ledger, cache_db=cache_db)
        assert third.deleted == (glossary_id,)
        assert ledger.known_units()[GLOSSARY_KEY] == (glossary_id, True)
        assert len(third.records) == 21
        assert index.sync(list(third.records)) == {"changed": True, "records": 21}

        assert ledger.corpus_revision() != corpus_revision_2
        assert index.index_revision != index_revision_2
        with pytest.raises(BadRef):
            index.read(glossary_id)
        gone = index.search(query="Derate")
        assert gone.lexical_records == derate_records_before - 1
        assert all(not hit.ref.startswith(f"{glossary_id} ") for hit in gone.hits)
        # The retired anchor still resolves to its exact text, marked retired.
        retired = index.read(f"{glossary_id} p1")
        assert retired.body == "Desk glossary"
        assert any("retired" in label for label in retired.labels)
    finally:
        index.close()
        ledger.close()


# -- issue #45: a converter or dating bump drops every digest's credit -----


def test_a_converter_or_dating_bump_drops_every_digests_coverage_credit(tmp_path, monkeypatch):
    """Carried over from #31's coverage checkbox ("a conversion or dating
    change each drop the digest's credit"), driven through the real adapter
    and its hand-bumped versions rather than a monkeypatched ``converter``/
    ``dated`` passed straight to the ledger."""
    corpus = tmp_path / "sources"
    shutil.copytree(SOURCES, corpus)
    ledger = Ledger(tmp_path / "ledger.db")
    cache_db = tmp_path / "store.db"
    index = Index(tmp_path / "index.db", ledger=ledger, embedder=FakeEmbedder())
    window = ("2000-01-01", "2099-12-31")  # wide enough to span every fixture date

    def synced_digest(ref):
        return make_note(
            ref, ["Digest body."], type="digest", window=window,
            corpus_revision=ledger.corpus_revision(), coverage_complete=True,
        )

    def covered_refs():
        return {row.ref for row in index.search(from_=window[0], to=window[1]).covered}

    try:
        first = sources.sync([corpus], ledger, cache_db=cache_db)
        index.sync(list(first.records))
        digest_1 = synced_digest("notes/digest/all-1.md")
        index.sync([*first.records, digest_1])
        assert covered_refs() == {digest_1.ref}

        # Bump a converter version: only its units re-convert, but the
        # corpus revision advances, so the digest at the old revision loses
        # credit even though no source content changed.
        monkeypatch.setitem(normalizer.CONVERTER_VERSIONS, "text", normalizer.CONVERTER_VERSIONS["text"] + 1)
        second = sources.sync([corpus], ledger, cache_db=cache_db)
        index.sync([*second.records, digest_1])
        assert covered_refs() == set()

        # A fresh digest at the new revision earns credit again.
        digest_2 = synced_digest("notes/digest/all-2.md")
        index.sync([*second.records, digest_1, digest_2])
        assert covered_refs() == {digest_2.ref}

        # Bump the dating version: no conversion re-runs, but every unit
        # re-versions, advancing the revision and dropping digest_2's
        # credit too.
        monkeypatch.setattr(dating, "DATING_VERSION", dating.DATING_VERSION + 1)
        third = sources.sync([corpus], ledger, cache_db=cache_db)
        index.sync([*third.records, digest_1, digest_2])
        assert covered_refs() == set()
    finally:
        index.close()
        ledger.close()

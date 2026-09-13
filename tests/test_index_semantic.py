"""``semantic=True`` fails loudly rather than degrading to lexical-only when
sqlite-vec cannot be used (issue #42): the module missing, or this Python's
``sqlite3`` unable to load it as an extension. ``semantic=False`` stays the
explicit, caller-chosen lexical-only mode and never imports ``sqlite_vec``.
"""

from __future__ import annotations

import sqlite3
import sys

import pytest

from strata.embeddings import FakeEmbedder
from strata.index import Index, SemanticUnavailable
from strata.ledger import Ledger

from factories import exact, make_source


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    yield led
    led.close()


def test_missing_sqlite_vec_module_raises_a_typed_error_naming_it(tmp_path, ledger, monkeypatch):
    monkeypatch.setitem(sys.modules, "sqlite_vec", None)
    with pytest.raises(SemanticUnavailable, match="sqlite-vec"):
        Index(tmp_path / "index.db", ledger=ledger, embedder=FakeEmbedder(), semantic=True)


@pytest.mark.parametrize("load_error", [AttributeError, sqlite3.NotSupportedError, sqlite3.OperationalError])
def test_a_failed_extension_load_raises_the_same_typed_error(tmp_path, ledger, monkeypatch, load_error):
    import sqlite_vec

    def boom(conn):
        raise load_error("simulated load failure")

    monkeypatch.setattr(sqlite_vec, "load", boom)
    with pytest.raises(SemanticUnavailable, match="sqlite-vec"):
        Index(tmp_path / "index.db", ledger=ledger, embedder=FakeEmbedder(), semantic=True)


def test_semantic_false_still_constructs_without_importing_sqlite_vec(tmp_path, ledger, monkeypatch):
    monkeypatch.setitem(sys.modules, "sqlite_vec", None)
    index = Index(tmp_path / "index.db", ledger=ledger, embedder=FakeEmbedder(), semantic=False)
    try:
        rec = make_source(ledger, "a.txt", ["The cutoff moved to eleven."], date=exact("2001-06-01"))
        index.sync([rec])
        reply = index.search("cutoff")
        assert [h.ref for h in reply.hits] == ["SRC-000001 p1"]
    finally:
        index.close()

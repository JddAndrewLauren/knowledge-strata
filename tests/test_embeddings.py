"""strata.embeddings: the real embedder's model cache lands at
``~/.strata/cache/models/`` (design.md "Multi-project": the model files
beside ``store.db``, one download per machine).

Hermetic: ``fastembed`` is replaced in ``sys.modules`` by a recorder before
the constructor imports it, so no model is downloaded and the real package
is never even imported; ``Path.home`` points at ``tmp_path``.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

from strata.embeddings import FastEmbedEmbedder, default_model_cache


class _RecordingTextEmbedding:
    calls: list[dict] = []

    def __init__(self, **kwargs):
        self.calls.append(kwargs)


def _fake_fastembed(monkeypatch) -> type[_RecordingTextEmbedding]:
    module = types.ModuleType("fastembed")
    module.TextEmbedding = _RecordingTextEmbedding
    monkeypatch.setitem(sys.modules, "fastembed", module)
    _RecordingTextEmbedding.calls = []
    return _RecordingTextEmbedding


def test_model_cache_defaults_to_the_user_home_and_is_created(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr(Path, "home", lambda: home)
    recorder = _fake_fastembed(monkeypatch)

    FastEmbedEmbedder()

    expected = home / ".strata" / "cache" / "models"
    assert default_model_cache() == expected
    assert expected.is_dir()
    assert recorder.calls == [{"model_name": "BAAI/bge-small-en-v1.5", "cache_dir": str(expected)}]


def test_an_explicit_cache_dir_overrides_the_default(tmp_path, monkeypatch):
    recorder = _fake_fastembed(monkeypatch)
    explicit = tmp_path / "elsewhere" / "models"

    FastEmbedEmbedder(cache_dir=explicit)

    assert explicit.is_dir()
    assert recorder.calls[0]["cache_dir"] == str(explicit)

"""strata.config: ``.strata/config.yaml`` in, one project's settings out
(design.md "Multi-project"). This loader is shared by ``strata.server``
(reads it fresh on every call) and, eventually, ``strata init`` (issue #33).
"""

from __future__ import annotations

import pytest

from strata.config import ConfigError, load
from strata.index import DEFAULT_CHUNK_TOKENS


def _write(folder, text: str) -> None:
    (folder / ".strata").mkdir(parents=True, exist_ok=True)
    (folder / ".strata" / "config.yaml").write_text(text, encoding="utf-8")


def test_loads_corpus_manuscript_and_chunk_tokens(tmp_path):
    _write(tmp_path, "corpus:\n  - ./sources\n  - /abs/other\nmanuscript: ./manuscript\nchunk_tokens: 1000\n")
    cfg = load(tmp_path)
    assert cfg.corpus == ("./sources", "/abs/other")
    assert cfg.manuscript == "./manuscript"
    assert cfg.chunk_tokens == 1000


def test_manuscript_and_chunk_tokens_are_optional(tmp_path):
    _write(tmp_path, "corpus:\n  - ./sources\n")
    cfg = load(tmp_path)
    assert cfg.manuscript is None
    assert cfg.chunk_tokens == DEFAULT_CHUNK_TOKENS


def test_corpus_defaults_to_empty(tmp_path):
    _write(tmp_path, "manuscript: ./manuscript\n")
    cfg = load(tmp_path)
    assert cfg.corpus == ()


def test_missing_config_names_init(tmp_path):
    with pytest.raises(ConfigError, match="strata init"):
        load(tmp_path)


def test_unknown_key_is_named(tmp_path):
    _write(tmp_path, "corpus: []\nbogus: true\n")
    with pytest.raises(ConfigError, match="bogus"):
        load(tmp_path)


@pytest.mark.parametrize(
    "text",
    [
        "corpus: not-a-list\n",
        "corpus: [1, 2]\n",
        "manuscript: 3\n",
        "corpus: []\nchunk_tokens: 0\n",
        "corpus: []\nchunk_tokens: -5\n",
        "corpus: []\nchunk_tokens: 1.5\n",
        "corpus: []\nchunk_tokens: true\n",
        "not a mapping\n",
    ],
)
def test_malformed_fields_are_rejected(tmp_path, text):
    _write(tmp_path, text)
    with pytest.raises(ConfigError):
        load(tmp_path)

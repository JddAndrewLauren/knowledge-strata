"""The embedder seam: a plain-callable interface `index.py` uses for the
semantic half of hybrid search (design.md "Search";
docs/research/python-stack.md).

Two implementations: :class:`FakeEmbedder`, a deterministic dict-backed
stand-in the hermetic suite wires with specific topics, and
:class:`FastEmbedEmbedder`, the real ``fastembed`` ``bge-small-en-v1.5``
model used outside tests. Both expose the same three things `index.py`
needs: a stable ``model_id`` and ``dim`` (stored at sync time so a model
change forces re-embedding), ``embed_passages`` and ``embed_query``. The
bge query instruction is added on the query side only, so it is reversible
without touching stored passage vectors (python-stack.md ss3).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol
from pathlib import Path
import hashlib
import sqlite3
import struct

QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "

Vector = tuple[float, ...]


class Embedder(Protocol):
    model_id: str
    dim: int

    def embed_passages(self, texts: Sequence[str]) -> list[Vector]: ...

    def embed_query(self, text: str) -> Vector: ...


class FakeEmbedder:
    """Deterministic, dict-backed (the issue brief's words): tests assign a
    ``topic`` label to specific texts so they land on the same one-hot
    direction and therefore have cosine similarity exactly 1 to a query
    given the same topic. Any untagged text gets one shared "no topic"
    direction, orthogonal to every real topic, so an untagged query never
    accidentally outranks a wired one and untagged passages never
    contend for a wired query's top-k on their own merits - only as
    zero-similarity padding once the wired winners are exhausted.

    Not a semantic model: it stands in for one so `index.py`'s ranking,
    deduplication and budget arithmetic can be tested without a network
    call or a model download (CONTEXT.md: hermetic).
    """

    model_id = "fake-v1"

    def __init__(self, topics: dict[str, str] | None = None, *, dim: int = 32):
        self.dim = dim
        self._topic_of_text = dict(topics or {})
        self._topic_index: dict[str, int] = {}
        self.passage_calls: list[str] = []
        self.query_calls: list[str] = []

    def place(self, text: str, topic: str) -> None:
        """Wire ``text`` to ``topic`` after construction (tests that build a
        fixture incrementally)."""
        self._topic_of_text[text] = topic

    def _topic_vector(self, topic: str) -> Vector:
        index = self._topic_index.setdefault(topic, len(self._topic_index))
        if index >= self.dim - 1:
            raise ValueError(f"FakeEmbedder(dim={self.dim}) has no room for topic {topic!r}")
        vector = [0.0] * self.dim
        vector[index] = 1.0
        return tuple(vector)

    def _default_vector(self) -> Vector:
        vector = [0.0] * self.dim
        vector[-1] = 1.0
        return tuple(vector)

    def _vector(self, text: str) -> Vector:
        topic = self._topic_of_text.get(text)
        return self._topic_vector(topic) if topic is not None else self._default_vector()

    def embed_passages(self, texts: Sequence[str]) -> list[Vector]:
        texts = list(texts)
        self.passage_calls.extend(texts)
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> Vector:
        self.query_calls.append(text)
        return self._vector(text)


class FastEmbedEmbedder:
    """``fastembed`` ``BAAI/bge-small-en-v1.5`` (384-d), for real use. Never
    imported by the hermetic suite: constructing one downloads model files on
    first use, which the ordinary test suite refuses to do (CONTEXT.md:
    hermetic). Passages are embedded bare; the bge query instruction is
    added on the query side only (python-stack.md ss3, "optional" prefix,
    "slight degradation" without it)."""

    model_id = "bge-small-en-v1.5"
    dim = 384

    def __init__(self, *, cache_dir: str | Path | None = None):
        from fastembed import TextEmbedding

        cache_dir = Path(cache_dir) if cache_dir else Path.home() / ".strata" / "cache" / "models"
        cache_dir.mkdir(parents=True, exist_ok=True)
        self._model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5", cache_dir=str(cache_dir))

    def embed_passages(self, texts: Sequence[str]) -> list[Vector]:
        return [tuple(float(x) for x in vec) for vec in self._model.embed(list(texts))]

    def embed_query(self, text: str) -> Vector:
        vec = next(iter(self._model.embed([QUERY_INSTRUCTION + text])))
        return tuple(float(x) for x in vec)


class CachedEmbedder:
    """Reusable per-user passage vectors; connections stay in the calling thread."""

    def __init__(self, embedder: Embedder, path: str | Path):
        self._embedder = embedder
        self.path = Path(path)
        self.model_id, self.dim = embedder.model_id, embedder.dim

    def embed_query(self, text: str) -> Vector:
        return self._embedder.embed_query(text)

    def embed_passages(self, texts: Sequence[str]) -> list[Vector]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=60)
        try:
            with connection:
                connection.execute("CREATE TABLE IF NOT EXISTS embeddings (model TEXT, dim INTEGER, hash TEXT, "
                                   "vector BLOB NOT NULL, PRIMARY KEY(model, dim, hash))")
                keys = [hashlib.sha256(text.encode('utf-8')).hexdigest() for text in texts]
                vectors = {}
                missing = {}
                for key, text in zip(keys, texts):
                    if key in vectors or key in missing:
                        continue
                    row = connection.execute("SELECT vector FROM embeddings WHERE model=? AND dim=? AND hash=?",
                                             (self.model_id, self.dim, key)).fetchone()
                    if row:
                        vectors[key] = struct.unpack(f'<{self.dim}f', row[0])
                    else:
                        missing[key] = text
                items = list(missing.items())
                for start in range(0, len(items), 64):
                    batch = items[start:start + 64]
                    fresh = self._embedder.embed_passages([text for _, text in batch])
                    if len(fresh) != len(batch):
                        raise ValueError('embedder returned an incomplete batch')
                    for (key, _), vector in zip(batch, fresh):
                        if len(vector) != self.dim:
                            raise ValueError('embedder returned the wrong vector dimension')
                        blob = struct.pack(f'<{self.dim}f', *vector)
                        vectors[key] = struct.unpack(f'<{self.dim}f', blob)
                        connection.execute("INSERT OR REPLACE INTO embeddings VALUES (?, ?, ?, ?)",
                                           (self.model_id, self.dim, key, blob))
                return [vectors[key] for key in keys]
        finally:
            connection.close()

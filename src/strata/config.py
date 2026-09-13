"""strata.config: ``.strata/config.yaml`` in, one project's settings out
(design.md "Multi-project"; CONTEXT.md "Corpus").

Per project: the corpus roots and optional manuscript path the user typed,
plus an optional ``chunk_tokens`` (design.md: "init writes only paths").
``strata init`` (issue #33) writes this file; :mod:`strata.server` reads it
back on every call so the corpus stays "fresh as of this call" - the one
loader both commands share, so a key ``init`` would never write is rejected
here too.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from strata.index import DEFAULT_CHUNK_TOKENS

_KNOWN_KEYS = {"corpus", "manuscript", "chunk_tokens"}


class ConfigError(ValueError):
    """``.strata/config.yaml`` is missing, or does not load: an unknown key,
    or a field of the wrong shape. The message names the key or the file."""


@dataclass(frozen=True)
class ProjectConfig:
    """Paths exactly as typed (design.md: "init writes only paths");
    resolving a relative one against the project folder is the caller's job."""

    corpus: tuple[str, ...]
    manuscript: str | None
    chunk_tokens: int


def load(project_folder: str | Path) -> ProjectConfig:
    """``.strata/config.yaml`` under ``project_folder``."""
    path = Path(project_folder) / ".strata" / "config.yaml"
    if not path.exists():
        raise ConfigError(f"no {path}: run `strata init` first")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: not a mapping of fields")
    unknown = sorted(set(data) - _KNOWN_KEYS)
    if unknown:
        raise ConfigError(f"{path}: unknown config key(s): {', '.join(unknown)}")

    corpus = data.get("corpus", [])
    if not isinstance(corpus, list) or not all(isinstance(item, str) for item in corpus):
        raise ConfigError(f"{path}: corpus must be a list of paths")
    manuscript = data.get("manuscript")
    if manuscript is not None and not isinstance(manuscript, str):
        raise ConfigError(f"{path}: manuscript must be a path")
    chunk_tokens = data.get("chunk_tokens", DEFAULT_CHUNK_TOKENS)
    if not isinstance(chunk_tokens, int) or isinstance(chunk_tokens, bool) or chunk_tokens <= 0:
        raise ConfigError(f"{path}: chunk_tokens must be a positive integer")

    return ProjectConfig(corpus=tuple(corpus), manuscript=manuscript, chunk_tokens=chunk_tokens)

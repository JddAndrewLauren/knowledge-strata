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

from collections.abc import Sequence
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
    """Paths exactly as typed (design.md: "init writes only paths"), with
    the two accessors that resolve them against the project folder - the
    one place both the server and the CLI get their corpus roots and
    manuscript path from."""

    corpus: tuple[str, ...]
    manuscript: str | None
    chunk_tokens: int

    def corpus_roots(self, project_folder: Path) -> list[Path]:
        return [_resolve(project_folder, root) for root in self.corpus]

    def manuscript_path(self, project_folder: Path) -> Path | None:
        return _resolve(project_folder, self.manuscript) if self.manuscript else None


def _resolve(folder: Path, maybe_relative: str) -> Path:
    path = Path(maybe_relative)
    return path if path.is_absolute() else folder / path


def load(project_folder: str | Path) -> ProjectConfig:
    """``.strata/config.yaml`` under ``project_folder``."""
    path = Path(project_folder) / ".strata" / "config.yaml"
    if not path.exists():
        raise ConfigError(f"no {path}: run `strata init` first")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return validate(data, path=path)


def validate(data, *, path="config") -> ProjectConfig:
    """Validate proposed settings without publishing them to disk."""
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


def write(project_folder: str | Path, *, corpus: Sequence[str], manuscript: str | None) -> None:
    """``strata init``'s half of this module: replace ``.strata/config.yaml``
    with ``corpus`` and, only if given, ``manuscript`` - and nothing else
    (design.md "init writes only paths"). Flags are the whole truth (issue
    #33): a call with no ``manuscript`` drops one written by an earlier call.
    ``corpus`` is whatever the caller settled on - the list typed this run,
    or the one already on file when no ``--corpus`` was typed (an empty
    corpus cannot be typed, so an absent flag never empties it). ``chunk_
    tokens`` is never written here, only ever hand-edited."""
    path = Path(project_folder) / ".strata" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    data: dict[str, object] = {"corpus": list(corpus)}
    if manuscript is not None:
        data["manuscript"] = manuscript
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

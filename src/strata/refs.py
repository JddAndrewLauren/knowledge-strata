"""Parse and render refs by kind. No dependencies, no filesystem.

A ref is the string that addresses something readable (CONTEXT.md). Three
shapes, and the parser never guesses a kind: a text is a source ref if and
only if it starts with ``SRC-``; a text carrying ``#`` is a manuscript ref
(path plus heading); everything else is a bare project-relative path, which
may be a note or a manuscript file. Deciding which is the resolver's job.

The grammar lives here and nowhere else: every module renders through
``render`` and reads through ``parse``, and the anchor form ``p17`` has one
source, the ``anchor``/``split_anchor`` pair. A rewrite of the ``SRC-`` slice
of memoria's ``references.py`` (wayfinder #6); ``is_durable`` is not built
(wayfinder #7).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath


class BadRef(ValueError):
    """A text that is not a ref. The message names the offending part."""


@dataclass(frozen=True)
class SourceRef:
    """``SRC-000184``, one paragraph of it, or a range of paragraphs.

    ``anchor`` is the paragraph's durable number (an id, not a position);
    ``None`` names the whole record. ``end`` closes an inclusive range
    (``p17-22``); ``tail`` opens one (``p17-``, the current tail). Whether
    ``end`` comes before ``anchor`` in document order is the ledger's and the
    index's question, not this module's.
    """

    id: str
    anchor: int | None = None
    end: int | None = None
    tail: bool = False

    def __post_init__(self):
        if self.anchor is None and (self.end is not None or self.tail):
            raise BadRef(f"a range needs a starting anchor: {self!r}")
        if self.end is not None and self.tail:
            raise BadRef(f"a range is closed or open, not both: {self!r}")


@dataclass(frozen=True)
class ManuscriptRef:
    """``manuscript/ch24.md # The Letter``: a heading section of a live file.

    Positional, never durable. ``heading`` is the text as written;
    ``occurrence`` is the ``(2)`` suffix for a repeated heading, 1 when none.
    There is no paragraph part, ever.
    """

    path: str
    heading: str
    occurrence: int = 1


@dataclass(frozen=True)
class PathRef:
    """A bare project-relative path: a note, or a manuscript file whole."""

    path: str


Ref = SourceRef | ManuscriptRef | PathRef


# Six digits, strictly. `SRC-184` is a typo for a different record as easily
# as a short form of this one.
_ID = re.compile(r"(?P<id>SRC-\d{6})", re.IGNORECASE)
_ID_SHAPED = re.compile(r"(?P<id>SRC-\d*)(?P<rest>.*)", re.IGNORECASE | re.DOTALL)
# The anchor after the id: `p17`, ` P17`, `p17`, ` \u00b617`, `-p17`, and a
# range tail `-22` or `-`. A bare number is refused: it is as easily a typo
# for another id as a paragraph.
_PARAGRAPH = re.compile(
    r"[\s-]*(?:p|\u00b6)\s*(?P<anchor>\d+)(?:(?P<dash>-)(?P<end>\d+)?)?",
    re.IGNORECASE,
)
_ANCHOR = re.compile(r"(?:p|\u00b6)\s*(?P<number>\d+)", re.IGNORECASE)
_OCCURRENCE = re.compile(r"\((?P<n>\d+)\)$")


def anchor(number: int) -> str:
    """The anchor form of a paragraph number: ``p17``.

    The single source of that form in both directions. ``render`` writes
    through it and ``split_anchor`` reads through it, so the grammar can only
    change in one place.
    """
    return f"p{number}"


def split_anchor(text: str) -> int:
    """The paragraph number an anchor names: ``p17``, ``P17`` or ``\u00b617``."""
    match = _ANCHOR.fullmatch(text.strip())
    if match is None:
        raise BadRef(f"not an anchor: {text!r} - a paragraph is written p17")
    return int(match.group("number"))


def parse(text: str) -> Ref:
    """Read a ref's shape off the text itself.

    Accepts, for a source record (canonical form first):

        SRC-000184 p17      one paragraph
        SRC-000184 P17
        SRC-000184p17
        SRC-000184 \u00b617
        src-000184-p17      a search hit fed straight back
        SRC-000184          the whole record
        SRC-000184 p17-22   an inclusive range
        SRC-000184 p17-     the open-ended tail

    ``path # heading`` and ``path # heading (2)`` are manuscript refs; any
    other text is a project-relative path and must pass the refusals in
    ``_repository_path``.
    """
    text = text.strip()
    if not text:
        raise BadRef("empty ref")
    shaped = _ID_SHAPED.fullmatch(text)
    if shaped:
        return _source(shaped.group("id"), shaped.group("rest"))
    path, hash_, heading = text.partition("#")
    if hash_:
        return _manuscript(path, heading)
    return PathRef(_repository_path(text))


def _source(id: str, rest: str) -> SourceRef:
    if not _ID.fullmatch(id):
        raise BadRef(f"malformed source id {id!r} - six digits like SRC-000184")
    id = id.upper()
    if not rest.strip():
        return SourceRef(id)
    match = _PARAGRAPH.fullmatch(rest)
    if match is None:
        raise BadRef(
            f"not a paragraph of {id}: {rest.strip()!r} - a paragraph is written "
            f"{id} p17, a range {id} p17-22 or {id} p17-"
        )
    end = match.group("end")
    return SourceRef(
        id,
        anchor=int(match.group("anchor")),
        end=int(end) if end is not None else None,
        tail=match.group("dash") is not None and end is None,
    )


def _manuscript(path: str, heading: str) -> ManuscriptRef:
    path = _repository_path(path.strip())
    heading = heading.strip()
    if not heading:
        raise BadRef(f"empty heading after {path!r} #")
    occurrence = 1
    match = _OCCURRENCE.search(heading)
    if match:
        occurrence = int(match.group("n"))
        if occurrence < 1:
            raise BadRef(f"heading occurrence must be 1 or more: {heading!r}")
        heading = heading[: match.start()].rstrip()
        if not heading:
            raise BadRef(f"empty heading before the occurrence suffix: {match.group(0)!r}")
    return ManuscriptRef(path, heading, occurrence)


def _repository_path(text: str) -> str:
    """A project-relative POSIX path, or a refusal.

    A check on the ref, before anything touches the filesystem: absolute
    paths, drive letters and ``..`` components are refused here so no caller
    has to remember to. Backslashes are refused outright rather than
    normalized: ``docs\\..\\x`` is one component on POSIX and three on
    Windows, so a rule that normalized it would confine a read on one
    machine and not another.
    """
    if not text:
        raise BadRef("empty path")
    if "\\" in text:
        raise BadRef(f"not a project-relative path: {text!r} - use / as the separator")
    path = PurePosixPath(text)
    if not path.parts:
        raise BadRef(f"not a file: {text!r}")
    if path.is_absolute():
        raise BadRef(f"not a project-relative path: {text!r} - absolute paths are refused")
    if ":" in path.parts[0]:
        raise BadRef(f"not a project-relative path: {text!r} - drive letters are refused")
    if ".." in path.parts:
        raise BadRef(f"path escapes the project: {text!r} - '..' is refused")
    return str(path)


def render(ref: Ref) -> str:
    """The canonical way to write a ref: ASCII, ``SRC-000184 p17``."""
    match ref:
        case SourceRef(id, None, _, _):
            return id
        case SourceRef(id, number, None, False):
            return f"{id} {anchor(number)}"
        case SourceRef(id, number, None, True):
            return f"{id} {anchor(number)}-"
        case SourceRef(id, number, end, _):
            return f"{id} {anchor(number)}-{end}"
        case ManuscriptRef(path, heading, 1):
            return f"{path} # {heading}"
        case ManuscriptRef(path, heading, occurrence):
            return f"{path} # {heading} ({occurrence})"
        case PathRef(path):
            return path
    raise TypeError(f"not a ref: {ref!r}")

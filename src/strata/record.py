"""The Record interface: the only thing the index accepts.

One frozen dataclass per value. Per-kind rules are rejected at construction
rather than documented, so nothing downstream dispatches on a Record union
(wayfinder #6, decision 10). ``Date`` is a value object because the dating
module returns exactly this shape and the rule "no invented date" lives in
one place (#8 added ``granularity``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date as _date
from typing import Literal

from strata import refs

Kind = Literal["source", "note", "manuscript"]
Confidence = Literal["exact", "inferred", "unknown"]
Granularity = Literal["day", "month", "year"]

KINDS = ("source", "note", "manuscript")
CONFIDENCES = ("exact", "inferred", "unknown")
GRANULARITIES = ("day", "month", "year")

_ISO_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
# A note type is its folder name under notes/: a singular slug (#7).
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


def _iso_day(text: str, field: str) -> _date:
    """A full ISO day, or a ValueError naming the field."""
    if not _ISO_DAY.fullmatch(text):
        raise ValueError(f"{field} must be a full ISO day (YYYY-MM-DD), not {text!r}")
    try:
        return _date.fromisoformat(text)
    except ValueError as error:
        raise ValueError(f"{field} is not a real day: {text!r}") from error


@dataclass(frozen=True)
class Date:
    """An ISO-sortable date, its confidence, its granularity and the source's
    own wording. Empty exactly when confidence is ``unknown``; a partial date
    stores the first of its period so it sorts, and says so in ``granularity``.
    """

    iso: str
    confidence: Confidence
    granularity: Granularity
    text: str

    def __post_init__(self):
        if self.confidence not in CONFIDENCES:
            raise ValueError(f"confidence must be one of {CONFIDENCES}, not {self.confidence!r}")
        if self.granularity not in GRANULARITIES:
            raise ValueError(f"granularity must be one of {GRANULARITIES}, not {self.granularity!r}")
        if (self.iso == "") != (self.confidence == "unknown"):
            raise ValueError(
                f"iso is empty exactly when confidence is unknown: iso={self.iso!r}, "
                f"confidence={self.confidence!r}"
            )
        if self.iso == "":
            return
        day = _iso_day(self.iso, "iso")
        if self.confidence == "exact" and self.granularity != "day":
            raise ValueError(f"an exact date has day granularity, not {self.granularity!r}")
        if self.granularity == "month" and day.day != 1:
            raise ValueError(f"a month-granularity date stores the first of its month, not {self.iso!r}")
        if self.granularity == "year" and (day.month, day.day) != (1, 1):
            raise ValueError(f"a year-granularity date stores the first of its year, not {self.iso!r}")


@dataclass(frozen=True)
class Record:
    """Everything the index knows arrives as one of these.

    ``type`` is the note's folder under ``notes/`` (open vocabulary; ``None``
    for a top-level note). ``aliases``, ``window`` and ``warnings`` are note
    fields; ``corpus_revision`` and ``coverage_complete`` are digest fields.
    Impossible combinations raise here.
    """

    ref: str
    kind: Kind
    date: Date
    title: str
    paragraphs: tuple[str, ...]
    type: str | None = None
    aliases: tuple[str, ...] = ()
    window: tuple[str, str] | None = None
    corpus_revision: str | None = None
    coverage_complete: bool | None = None
    warnings: tuple[str, ...] = ()

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, not {self.kind!r}")
        if not self.title.strip():
            raise ValueError(f"title is required and never empty: {self.title!r}")
        self._check_ref()
        if self.kind != "note":
            for field in ("type", "window", "corpus_revision", "coverage_complete"):
                if getattr(self, field) is not None:
                    raise ValueError(f"{field} is a note field; this record is a {self.kind}")
            for field in ("aliases", "warnings"):
                if getattr(self, field):
                    raise ValueError(f"{field} is a note field; this record is a {self.kind}")
            return
        if self.type is not None and not _SLUG.fullmatch(self.type):
            raise ValueError(f"type is a folder name, a singular slug, not {self.type!r}")
        if self.type != "digest":
            for field in ("corpus_revision", "coverage_complete"):
                if getattr(self, field) is not None:
                    raise ValueError(f"{field} is a digest field; this note's type is {self.type!r}")
        if self.corpus_revision is not None and not self.corpus_revision:
            raise ValueError("corpus_revision is set but empty")
        if self.coverage_complete is not None and not isinstance(self.coverage_complete, bool):
            raise ValueError(f"coverage_complete must be a bool, not {self.coverage_complete!r}")
        if self.window is not None:
            self._check_window()

    def _check_ref(self):
        """The ref's shape follows the kind (CONTEXT.md: Kind decides the ref
        shape), and it is stored canonically because the index keys on it."""
        if not self.ref:
            raise ValueError("ref is required")
        parsed = refs.parse(self.ref)
        match self.kind, parsed:
            case "source", refs.SourceRef(anchor=None):
                pass
            case "source", refs.SourceRef():
                raise ValueError(f"a source record's ref names the whole record, not a paragraph: {self.ref!r}")
            case "note", refs.PathRef():
                pass
            case "manuscript", refs.PathRef() | refs.ManuscriptRef():
                pass
            case _:
                raise ValueError(
                    f"a {self.kind} record cannot carry a {type(parsed).__name__}: {self.ref!r}"
                )
        canonical = refs.render(parsed)
        if canonical != self.ref:
            raise ValueError(f"ref must be canonical: {self.ref!r} renders as {canonical!r}")

    def _check_window(self):
        if not (isinstance(self.window, tuple) and len(self.window) == 2):
            raise ValueError(f"window is a (from, to) pair of ISO days, not {self.window!r}")
        start, end = self.window
        if _iso_day(start, "window from") > _iso_day(end, "window to"):
            raise ValueError(f"window from must not follow to: {self.window!r}")


_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def first_sentence(paragraphs, cap: int = 120) -> str:
    """The extractive title of last resort, the same for every kind.

    The first sentence of the first non-empty paragraph, cut at ``cap``
    characters on a word boundary. Empty only when no paragraph has text.
    """
    for paragraph in paragraphs:
        text = " ".join(paragraph.split())
        if not text:
            continue
        match = _SENTENCE_END.search(text)
        if match:
            text = text[: match.end()]
        if len(text) > cap:
            cut = text.rfind(" ", 0, cap + 1)
            text = text[:cut] if cut > 0 else text[:cap]
        return text.rstrip()
    return ""

"""Record and Date builders shared by the index tests.

The index takes Records and nothing else (design.md); these build them
directly; no corpus adapter exists yet or is needed here. A ``source``
Record needs a ledger-assigned id and durable per-paragraph anchors (the
hit citation and retired-text acceptance criteria need real anchors), so
``make_source`` drives a real :class:`~strata.ledger.Ledger` the same way
the future sources adapter will: register the raw path, then align its
paragraphs, then build the Record from the id and anchors that comes back.
"""

from __future__ import annotations

import hashlib

from strata.ledger import Ledger
from strata.record import Date, Record, first_sentence


def exact(iso: str, text: str | None = None) -> Date:
    return Date(iso=iso, confidence="exact", granularity="day", text=text if text is not None else iso)


def inferred_day(iso: str, text: str) -> Date:
    return Date(iso=iso, confidence="inferred", granularity="day", text=text)


def inferred_month(iso_month_start: str, text: str) -> Date:
    return Date(iso=iso_month_start, confidence="inferred", granularity="month", text=text)


def inferred_year(iso_year_start: str, text: str) -> Date:
    return Date(iso=iso_year_start, confidence="inferred", granularity="year", text=text)


def unknown() -> Date:
    return Date(iso="", confidence="unknown", granularity="day", text="")


def _sha256(paragraphs: list[str]) -> str:
    return hashlib.sha256("\x00".join(paragraphs).encode("utf-8")).hexdigest()


def make_source(
    ledger: Ledger,
    path: str,
    paragraphs: list[str],
    *,
    date: Date = None,
    title: str | None = None,
) -> Record:
    """A source Record for raw unit ``path``, ledger-registered and aligned
    so its paragraphs carry real, durable anchors."""
    if date is None:
        date = unknown()
    unit_id = ledger.register([path])[path]
    ledger.align(path, _sha256(paragraphs), paragraphs, converter="text", at=date.iso)
    return Record(
        ref=unit_id,
        kind="source",
        date=date,
        title=title if title is not None else first_sentence(paragraphs),
        paragraphs=tuple(paragraphs),
    )


def make_note(
    path: str,
    paragraphs: list[str],
    *,
    type: str | None = None,
    aliases: tuple[str, ...] = (),
    window: tuple[str, str] | None = None,
    corpus_revision: str | None = None,
    coverage_complete: bool | None = None,
    warnings: tuple[str, ...] = (),
    date: Date = None,
    title: str | None = None,
) -> Record:
    if date is None:
        date = unknown()
    return Record(
        ref=path,
        kind="note",
        date=date,
        title=title if title is not None else first_sentence(paragraphs),
        paragraphs=tuple(paragraphs),
        type=type,
        aliases=aliases,
        window=window,
        corpus_revision=corpus_revision,
        coverage_complete=coverage_complete,
        warnings=warnings,
    )


def make_manuscript(path: str, paragraphs: list[str], *, date: Date = None, title: str | None = None) -> Record:
    if date is None:
        date = unknown()
    return Record(
        ref=path,
        kind="manuscript",
        date=date,
        title=title if title is not None else first_sentence(paragraphs),
        paragraphs=tuple(paragraphs),
    )

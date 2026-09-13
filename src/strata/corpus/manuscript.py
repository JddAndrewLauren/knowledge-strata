"""Manuscript adapter: the manuscript folder in, ``Record``s out (design.md,
"Module map"; CONTEXT.md, "Positional ref").

Heading-aware, markdown only. One Record per ``#``/``##`` heading: it runs
from its heading to the next heading of equal or shallower depth, nested
subheadings included, so a repeated heading text (``Morning``, ``Morning
(2)``) gets an occurrence suffix in document order. The file itself is also
a Record, whose paragraphs are the whole file. Dating runs once per file -
manuscript chapters run only the first-line and path rungs (CONTEXT.md,
"Dating strategy") - and every Record from that file shares its date; there
is no finer-grained dating than the chapter. Dotfiles are skipped silently,
as the sources adapter skips them. A file that is not markdown (docx, a
Scrivener export) is a ``Skip`` with a reason; the walk never aborts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from strata import dating, normalizer, refs
from strata.record import Record

_HEADING = re.compile(r"^(#{1,6})[ \t]+(\S.*)$")
_SECTION_DEPTHS = (1, 2)


@dataclass(frozen=True)
class Skip:
    """A manuscript file that produced no Record, and why (CONTEXT.md,
    "Skip"): not markdown, or refused by the normalizer."""

    ref: str
    reason: str


@dataclass(frozen=True)
class ReadReport:
    """One folder's outcome: Records in file, then document, order, plus
    every skipped file - the walk never aborts on one."""

    records: tuple[Record, ...]
    skipped: tuple[Skip, ...]


def read(folder: str | Path) -> ReadReport:
    folder = Path(folder)
    records: list[Record] = []
    skipped: list[Skip] = []
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        relative = path.relative_to(folder)
        if any(part.startswith(".") for part in relative.parts):
            continue
        ref_path = refs.render(refs.PathRef("manuscript/" + relative.as_posix()))
        if path.suffix.lower() != ".md":
            skipped.append(Skip(ref_path, f"not a markdown manuscript: {path.suffix or '(no extension)'}"))
            continue
        raw_bytes = path.read_bytes()
        result = normalizer.normalize(raw_bytes, ref_path)
        if isinstance(result, normalizer.Refusal):
            skipped.append(Skip(ref_path, result.reason))
            continue
        records.extend(_records_for_file(ref_path, raw_bytes, result))
    return ReadReport(tuple(records), tuple(skipped))


def _records_for_file(ref_path: str, raw_bytes: bytes, conversion: normalizer.Conversion) -> list[Record]:
    chapter_date = dating.date(dating.RawUnit(path=ref_path, kind="manuscript", content=raw_bytes))
    paragraphs = conversion.paragraphs
    records = [Record(ref=ref_path, kind="manuscript", date=chapter_date, title=conversion.title, paragraphs=paragraphs)]

    occurrences: dict[str, int] = {}
    for heading, start, end in _sections(paragraphs):
        occurrences[heading] = occurrences.get(heading, 0) + 1
        section_ref = refs.render(refs.ManuscriptRef(ref_path, heading, occurrences[heading]))
        records.append(Record(
            ref=section_ref, kind="manuscript", date=chapter_date, title=heading,
            paragraphs=paragraphs[start:end],
        ))
    return records


def _sections(paragraphs: tuple[str, ...]) -> list[tuple[str, int, int]]:
    """``(heading text, start, end)`` for every ``#``/``##`` heading, in
    document order. ``end`` is the next heading of equal or shallower depth
    (or the end of the file); a deeper heading never closes it, so nested
    subheadings are included."""
    headings = []
    for index, paragraph in enumerate(paragraphs):
        match = _HEADING.match(paragraph)
        if match:
            headings.append((index, len(match.group(1)), match.group(2).strip()))

    sections = []
    for position, (index, depth, text) in enumerate(headings):
        if depth not in _SECTION_DEPTHS:
            continue
        end = len(paragraphs)
        for later_index, later_depth, _ in headings[position + 1:]:
            if later_depth <= depth:
                end = later_index
                break
        sections.append((text, index, end))
    return sections

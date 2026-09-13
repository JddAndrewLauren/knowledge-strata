"""Notes adapter: ``notes/**/*.md`` in, ``Record``s out (design.md, "Notes
frontmatter"; wayfinder #7).

Reads every note as it is. ``type`` is the folder name directly under
``notes/`` (``None`` for a top-level file such as ``project.md``). This is
the only code that parses the frontmatter contract - ``aliases``, ``window``,
``corpus_revision``, ``coverage_complete``, all optional. A note that fails
validation is indexed with a warning, not dropped, with only the offending
field cleared - so every rule ``Record`` enforces on a note is checked here
first, field by field, and ``Record`` never gets to reject a note whole.
``coverage_complete`` without ``corpus_revision`` is accepted by ``Record``
itself but earns no coverage, so it is this adapter's warning to raise
(record.py). ``project.md`` is also warned about past 2,000 words of body
text, frontmatter excluded (issue #29). Title is the H1, else the filename
stem; date is always unknown, since notes run no dating strategy. The body
is split into paragraphs like plain text; frontmatter is not a paragraph.
Bytes decode the way the normalizer decodes plain text (UTF-8, then cp1252),
so one cp1252 note never aborts the walk.
"""

from __future__ import annotations

import re
from datetime import date as _date
from datetime import datetime as _datetime
from pathlib import Path

import yaml

from strata import dating, refs
from strata.normalizer import decode_text, split_paragraphs
from strata.record import Record

_FRONTMATTER = re.compile(r"\A---\r?\n(?P<yaml>.*?)\r?\n---\r?\n?", re.DOTALL)
_H1 = re.compile(r"^#[ \t]+(\S.*)$")
_FIELDS = ("aliases", "window", "corpus_revision", "coverage_complete")
_DIGEST_FIELDS = ("corpus_revision", "coverage_complete")
_WORD_CAP = 2000


def read(folder: str | Path) -> tuple[Record, ...]:
    """Every ``*.md`` under ``folder`` (the project's ``notes/`` root), one
    Record each, in path order. Never refuses: a malformed note is indexed
    with a warning (design.md)."""
    folder = Path(folder)
    return tuple(_read_one(folder, path) for path in sorted(folder.rglob("*.md")))


def _read_one(folder: Path, path: Path) -> Record:
    relative = path.relative_to(folder)
    ref = refs.render(refs.PathRef("notes/" + relative.as_posix()))
    note_type = relative.parts[0] if len(relative.parts) > 1 else None

    text = decode_text(path.read_bytes())
    warnings: list[str] = []
    data, body = _split_frontmatter(text, ref, warnings)

    for key in data:
        if key not in _FIELDS:
            warnings.append(f"{ref}: unknown frontmatter field {key!r}; ignored")
    if note_type != "digest":
        for field in _DIGEST_FIELDS:
            if field in data:
                warnings.append(f"{ref}: {field} is a digest field; this note's type is {note_type!r}; cleared")
                data = {key: value for key, value in data.items() if key != field}
    aliases = _valid_aliases(data, ref, warnings)
    window = _valid_window(data, ref, warnings)
    corpus_revision = _valid_corpus_revision(data, ref, warnings)
    coverage_complete = _valid_coverage_complete(data, ref, warnings)
    if coverage_complete is not None and corpus_revision is None:
        warnings.append(f"{ref}: coverage_complete is set without corpus_revision; cleared")
        coverage_complete = None

    if relative == Path("project.md"):
        word_count = len(body.split())
        if word_count > _WORD_CAP:
            warnings.append(f"{ref}: body text is {word_count} words, over the 2,000-word cap")

    paragraphs = tuple(split_paragraphs(body))
    title = _title(paragraphs, path)
    note_date = dating.date(dating.RawUnit(path=ref, kind="note"))

    return Record(
        ref=ref, kind="note", date=note_date, title=title, paragraphs=paragraphs, type=note_type,
        aliases=aliases, window=window, corpus_revision=corpus_revision,
        coverage_complete=coverage_complete, warnings=tuple(warnings),
    )


def _split_frontmatter(text: str, ref: str, warnings: list[str]) -> tuple[dict, str]:
    match = _FRONTMATTER.match(text)
    if not match:
        return {}, text
    body = text[match.end():]
    try:
        data = yaml.safe_load(match.group("yaml"))
    except (yaml.YAMLError, ValueError):
        warnings.append(f"{ref}: frontmatter is not valid YAML; ignored")
        return {}, body
    if not isinstance(data, dict):
        warnings.append(f"{ref}: frontmatter is not a mapping of fields; ignored")
        return {}, body
    return data, body


def _title(paragraphs: tuple[str, ...], path: Path) -> str:
    for paragraph in paragraphs:
        match = _H1.match(paragraph)
        if match:
            return match.group(1).strip()
    return path.stem


def _valid_aliases(data: dict, ref: str, warnings: list[str]) -> tuple[str, ...]:
    if "aliases" not in data:
        return ()
    aliases = data["aliases"]
    if isinstance(aliases, list) and all(isinstance(a, str) for a in aliases):
        return tuple(aliases)
    warnings.append(f"{ref}: aliases is not a list; cleared")
    return ()


def _valid_window(data: dict, ref: str, warnings: list[str]) -> tuple[str, str] | None:
    if "window" not in data:
        return None
    window = data["window"]
    if isinstance(window, dict) and set(window) == {"from", "to"}:
        start, end = _as_iso_day(window["from"]), _as_iso_day(window["to"])
        if start is not None and end is not None and start <= end:
            return (start, end)
    warnings.append(f"{ref}: window is not a valid from/to pair; cleared")
    return None


def _as_iso_day(value: object) -> str | None:
    """A real calendar day as ``YYYY-MM-DD``: YAML already parsed a bare
    ``2001-06-30`` into a date; a quoted one must parse the same way."""
    if isinstance(value, _date) and not isinstance(value, _datetime):
        return value.isoformat()
    if isinstance(value, str) and len(value) == 10:
        try:
            return _date.fromisoformat(value).isoformat()
        except ValueError:
            return None
    return None


def _valid_corpus_revision(data: dict, ref: str, warnings: list[str]) -> str | None:
    if "corpus_revision" not in data:
        return None
    value = data["corpus_revision"]
    if isinstance(value, str) and value:
        return value
    warnings.append(f"{ref}: corpus_revision is not a non-empty string; cleared")
    return None


def _valid_coverage_complete(data: dict, ref: str, warnings: list[str]) -> bool | None:
    if "coverage_complete" not in data:
        return None
    value = data["coverage_complete"]
    if isinstance(value, bool):
        return value
    warnings.append(f"{ref}: coverage_complete is not true or false; cleared")
    return None

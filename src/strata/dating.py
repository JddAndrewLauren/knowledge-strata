"""strata.dating: raw unit in, ``Date`` out (design.md, "Dating").

Four internal strategies, tried in order, first hit wins, no disagreement
log: transport headers (email ``Date``, then ``Received``), the unit's first
line or leading heading, path patterns (filename, then folders up to the
corpus root), and document metadata (docx core properties, pdf Info/XMP).
The author's own words about a date beat every non-email piece of metadata,
filenames included, because text is tried before path and metadata. File
mtime is never a source. No ``dateutil`` fuzzy mode, no ``dateparser``
(wayfinder #8's resolution).

Converters do not touch date fields, so this module opens email, docx and
pdf bytes itself for headers and metadata rather than trusting a
normalizer's parse - the parsing and the "text beats metadata" rule live in
the one seam. The unit's words arrive either as the raw bytes of a text
file or as the converter's paragraphs (docx and pdf body text is the
normalizer's to extract, never this module's). Sources run the whole chain;
manuscript chapters run the first-line and path rungs only; notes are never
dated.
"""

from __future__ import annotations

import email
import email.message
import email.utils
import re
import zipfile
import zlib
from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime, timezone
from io import BytesIO
from pathlib import PurePosixPath
from xml.etree import ElementTree

from strata.normalizer import decode_text
from strata.record import KINDS, Date, Kind

UNKNOWN = Date("", "unknown", "day", "")

# --------------------------------------------------------------------------
# The raw unit
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class RawUnit:
    """What the dating module reads: the corpus-relative path, the kind
    (decides which strategies run), the raw bytes, and optionally the
    converter's paragraphs. Headers and metadata are read from the bytes
    here (nobody hands over a partial parse); the first-line rung reads the
    paragraphs when they are given, which is the only way a docx or pdf's
    own words reach it, and the raw bytes of a text file otherwise."""

    path: str
    kind: Kind
    content: bytes = b""
    paragraphs: tuple[str, ...] = ()

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, not {self.kind!r}")


def date(raw: RawUnit) -> Date:
    """The module's one entry point: first hit wins, nothing invented."""
    if raw.kind == "note":
        return UNKNOWN
    strategies = (
        (_email_headers, _first_line, _path, _metadata)
        if raw.kind == "source"
        else (_first_line, _path)
    )
    for strategy in strategies:
        hit = strategy(raw)
        if hit is not None:
            return hit
    return UNKNOWN


# --------------------------------------------------------------------------
# Strategy 1: transport headers (email only)
# --------------------------------------------------------------------------


def _email_headers(raw: RawUnit) -> Date | None:
    if not raw.path.lower().endswith(".eml"):
        return None
    message = email.message_from_bytes(raw.content)
    dates = _headers(message, "date")
    if dates:
        parsed = _parse_rfc5322(dates[0])
        if parsed is not None:
            return Date(parsed.date().isoformat(), "exact", "day", dates[0].strip())
    earliest = None
    for text in _headers(message, "received"):
        _, _, tail = text.rpartition(";")
        candidate = tail.strip() or text.strip()
        parsed = _parse_rfc5322(candidate)
        if parsed is not None and (earliest is None or _instant(parsed) < _instant(earliest[0])):
            earliest = (parsed, candidate)
    if earliest is not None:
        parsed, candidate = earliest
        return Date(parsed.date().isoformat(), "inferred", "day", f"Received {candidate}")
    return None


def _headers(message: email.message.Message, name: str) -> list[str]:
    """Every value of one header, in order, as real text. ``get()`` hands a
    header carrying raw 8-bit bytes back as an ``email.header.Header`` with
    the bytes already replaced (the corpus is ISO-8859-1 email), so the raw
    pairs are read instead: their surrogate escapes are the bytes, put back
    and decoded so the verbatim wording is an encodable string."""
    return [
        decode_text(value.encode("utf-8", "surrogateescape"))
        for key, value in message.raw_items()
        if key.lower() == name
    ]


def _parse_rfc5322(text: str) -> datetime | None:
    try:
        return email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None


def _instant(parsed: datetime) -> datetime:
    """One comparable key for ``Received`` timestamps: ``-0000`` and a
    missing zone parse naive, everything else aware, and the two cannot be
    compared directly. Naive is read as UTC, which is what ``-0000`` means."""
    if parsed.tzinfo is None:
        return parsed
    return parsed.astimezone(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------
# Date-shape recognition, shared by the first-line and path strategies
# --------------------------------------------------------------------------

_MONTHS = {
    name: number
    for number, names in enumerate(
        [
            ("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"),
            ("may",), ("june", "jun"), ("july", "jul"), ("august", "aug"),
            ("september", "sep", "sept"), ("october", "oct"), ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for name in names
}
_MONTH_PATTERN = "|".join(sorted(_MONTHS, key=len, reverse=True))

_ISO = re.compile(r"(?<!\d)(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})(?!\d)")
_DAY_MONTH_YEAR = re.compile(
    rf"(?<!\d)(?P<day>\d{{1,2}})(?:st|nd|rd|th)?[\s,]+(?P<month>{_MONTH_PATTERN})\.?,?\s+"
    rf"(?P<year>\d{{4}}|\d{{2}})(?!\d)",
    re.IGNORECASE,
)
_MONTH_DAY_YEAR = re.compile(
    rf"(?<!\d)(?P<month>{_MONTH_PATTERN})\.?\s+(?P<day>\d{{1,2}})(?:st|nd|rd|th)?,?\s+"
    rf"(?P<year>\d{{4}}|\d{{2}})(?!\d)",
    re.IGNORECASE,
)
_MONTH_YEAR = re.compile(
    rf"(?<!\d)(?P<month>{_MONTH_PATTERN})\.?[\s,]+(?P<year>\d{{4}})(?!\d)",
    re.IGNORECASE,
)
_NUMERIC = re.compile(r"(?<!\d)(?P<a>\d{1,2})[/.-](?P<b>\d{1,2})[/.-](?P<year>\d{4})(?!\d)")


@dataclass(frozen=True)
class _Shape:
    kind: str  # "day" | "numeric_ambiguous" | "month_year"
    source: str  # "iso" | "month_name" | "numeric" | "month_year"
    year: int
    month: int | None
    day: int | None
    match: re.Match
    year_digits: int


def _valid_day(year: int, month: int, day: int) -> bool:
    if not (1900 <= year <= 2100 and 1 <= month <= 12):
        return False
    try:
        _date(year, month, day)
    except ValueError:
        return False
    return True


def _expand_year(text: str) -> int:
    if len(text) == 4:
        return int(text)
    two_digit = int(text)
    return 2000 + two_digit if two_digit <= 68 else 1900 + two_digit


def _find_date_shape(text: str) -> _Shape | None:
    """The three explicit regex shapes (ISO, month-name, numeric with a
    four-digit year), plus a bare month-and-year heading, tried in that
    order so a full date is never mistaken for a coarser one."""
    match = _ISO.search(text)
    if match and _valid_day(int(match["year"]), int(match["month"]), int(match["day"])):
        return _Shape("day", "iso", int(match["year"]), int(match["month"]), int(match["day"]), match, 4)

    match = _DAY_MONTH_YEAR.search(text) or _MONTH_DAY_YEAR.search(text)
    if match:
        year = _expand_year(match["year"])
        month = _MONTHS[match["month"].lower()]
        day = int(match["day"])
        if _valid_day(year, month, day):
            return _Shape("day", "month_name", year, month, day, match, len(match["year"]))

    match = _NUMERIC.search(text)
    if match:
        a, b, year = int(match["a"]), int(match["b"]), int(match["year"])
        if a <= 12 and b <= 12:
            return _Shape("numeric_ambiguous", "numeric", year, None, None, match, 4)
        day, month = (a, b) if a > 12 else (b, a)
        if month <= 12 and _valid_day(year, month, day):
            return _Shape("day", "numeric", year, month, day, match, 4)

    match = _MONTH_YEAR.search(text)
    if match:
        return _Shape(
            "month_year", "month_year", int(match["year"]), _MONTHS[match["month"].lower()],
            None, match, 4,
        )

    return None


# --------------------------------------------------------------------------
# Strategy 2: the first line or leading heading
# --------------------------------------------------------------------------

_HEADING_MARKERS = re.compile(r"^#+\s*")
_WEEKDAY = re.compile(
    r"^(?:mon(?:day)?|tue(?:sday)?|wed(?:nesday)?|thu(?:rsday)?|fri(?:day)?|sat(?:urday)?|sun(?:day)?)"
    r"\.?,?\s+",
    re.IGNORECASE,
)
_LABEL = re.compile(r"^(?:date|written)\s*:\s*(?P<value>.+)$", re.IGNORECASE)
_CANDIDATE_CAP = 45


def _first_line(raw: RawUnit) -> Date | None:
    suffix = PurePosixPath(raw.path).suffix.lower()
    if suffix == ".eml":
        return None  # transport headers are email's one seam: no repair of a human-format Date
    if raw.paragraphs:
        text = "\n".join(raw.paragraphs)
    elif suffix in (".docx", ".pdf"):
        return None  # their words reach this rung only as the converter's paragraphs
    else:
        text = decode_text(raw.content)
    non_empty = [line for line in text.splitlines() if line.strip()]
    if not non_empty:
        return None
    candidates = [non_empty[0]]
    for line in non_empty[1:5]:
        label = _LABEL.match(line.strip())
        if label:
            candidates.append(label.group("value"))
    for candidate in candidates:
        hit = _body_hit(candidate)
        if hit is not None:
            return hit
    return None


def _body_hit(candidate: str) -> Date | None:
    stripped = _WEEKDAY.sub("", _HEADING_MARKERS.sub("", candidate.strip()))
    if len(stripped) > _CANDIDATE_CAP:
        return None
    shape = _find_date_shape(stripped)
    if shape is None:
        return None
    text = _HEADING_MARKERS.sub("", candidate.strip())
    if shape.kind == "numeric_ambiguous":
        return Date(
            f"{shape.year:04d}-01-01", "inferred", "year",
            f"{shape.year} (numeric date, day/month ambiguous)",
        )
    if shape.kind == "month_year":
        return Date(f"{shape.year:04d}-{shape.month:02d}-01", "inferred", "month", text)
    iso = f"{shape.year:04d}-{shape.month:02d}-{shape.day:02d}"
    core = stripped.rstrip(" \t.,;:")
    whole_line = shape.match.start() == 0 and shape.match.end() == len(core)
    exact = whole_line and shape.year_digits == 4
    return Date(iso, "exact" if exact else "inferred", "day", text)


# --------------------------------------------------------------------------
# Strategy 3: path patterns
# --------------------------------------------------------------------------


def _path(raw: RawUnit) -> Date | None:
    path = PurePosixPath(raw.path)
    hit = _filename_hit(path.stem)
    if hit is not None:
        return hit
    return _folder_hit(path.parts[:-1])


def _filename_hit(stem: str) -> Date | None:
    shape = _find_date_shape(stem)
    if shape is None:
        return None
    if shape.kind == "numeric_ambiguous":
        return Date(
            f"{shape.year:04d}-01-01", "inferred", "year",
            f"{shape.year} (numeric date, day/month ambiguous)",
        )
    if shape.kind == "month_year":
        return Date(
            f"{shape.year:04d}-{shape.month:02d}-01", "inferred", "month",
            f"{shape.year:04d}-{shape.month:02d} (filename)",
        )
    iso = f"{shape.year:04d}-{shape.month:02d}-{shape.day:02d}"
    confidence = "exact" if shape.source == "iso" else "inferred"
    return Date(iso, confidence, "day", f"{iso} (filename)")


def _folder_hit(folders: tuple[str, ...]) -> Date | None:
    """Filename first, then folders up to the corpus root, nearest first
    (design.md; wayfinder #8). A month folder's year comes from its parent."""
    month = None
    for folder in reversed(folders):
        if month is None:
            candidate_month = _MONTHS.get(folder.strip().lower())
            if candidate_month is not None:
                month = candidate_month
                continue
        year = _bare_year(folder)
        if year is not None:
            if month is not None:
                return Date(
                    f"{year:04d}-{month:02d}-01", "inferred", "month",
                    f"{year:04d}-{month:02d} (folder)",
                )
            return Date(f"{year:04d}-01-01", "inferred", "year", f"{year} (folder)")
    return None


def _bare_year(text: str) -> int | None:
    if re.fullmatch(r"\d{4}", text):
        year = int(text)
        if 1900 <= year <= 2100:
            return year
    return None


# --------------------------------------------------------------------------
# Strategy 4: document metadata (docx, pdf)
# --------------------------------------------------------------------------

_DCTERMS_NS = {"dcterms": "http://purl.org/dc/terms/"}
_W3CDTF_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})")


def _metadata(raw: RawUnit) -> Date | None:
    suffix = PurePosixPath(raw.path).suffix.lower()
    if suffix == ".docx":
        return _docx_created(raw.content)
    if suffix == ".pdf":
        return _pdf_metadata(raw.content)
    return None


def _docx_created(content: bytes) -> Date | None:
    try:
        with zipfile.ZipFile(BytesIO(content)) as archive:
            core = archive.read("docProps/core.xml")
    except (zipfile.BadZipFile, KeyError):
        return None
    created = ElementTree.fromstring(core).findtext("dcterms:created", namespaces=_DCTERMS_NS)
    if not created:
        return None
    iso = _w3cdtf_to_iso(created)
    if iso is None:
        return None
    return Date(iso, "inferred", "day", f"docx created {iso}")


def _w3cdtf_to_iso(text: str) -> str | None:
    """The day out of a W3CDTF/ISO 8601 timestamp (docx core properties,
    pdf XMP): ``2013-11-04T21:15:03Z`` -> ``2013-11-04``."""
    match = _W3CDTF_DAY.match(text)
    if not match or not _valid_day(int(match[1]), int(match[2]), int(match[3])):
        return None
    return f"{int(match[1]):04d}-{int(match[2]):02d}-{int(match[3]):02d}"


_PDF_CREATION = re.compile(rb"/CreationDate\s*\(D:(?P<date>[^)]*)\)")
_PDF_CREATOR = re.compile(rb"/Creator\s*\((?P<value>[^)]*)\)")
_PDF_PRODUCER = re.compile(rb"/Producer\s*\((?P<value>[^)]*)\)")
_PDF_XMP_CREATE = re.compile(rb"<xmp:CreateDate>(?P<date>[^<]*)</xmp:CreateDate>")
_PDF_TEXT_OPERATOR = re.compile(rb"\bTj\b|\bTJ\b")
_PDF_STREAM = re.compile(rb"\bstream\r?\n(?P<body>.*?)\r?\nendstream", re.DOTALL)


def _pdf_metadata(content: bytes) -> Date | None:
    scanner = any(
        b"scan" in match.group("value").lower()
        for match in (_PDF_CREATOR.search(content), _PDF_PRODUCER.search(content))
        if match
    )
    if scanner or not _pdf_has_text_layer(content):
        return None  # a scan's metadata names the scan, not the document
    match = _PDF_CREATION.search(content)
    if match:
        iso = _pdf_date_to_iso(match.group("date").decode("latin-1"))
        if iso:
            return Date(iso, "inferred", "day", f"pdf CreationDate {iso}")
    match = _PDF_XMP_CREATE.search(content)
    if match:
        iso = _w3cdtf_to_iso(match.group("date").decode("latin-1"))
        if iso:
            return Date(iso, "inferred", "day", f"pdf XMP CreateDate {iso}")
    return None


def _pdf_has_text_layer(content: bytes) -> bool:
    """A text-showing operator in any content stream. Streams are nearly
    always ``/FlateDecode``, so each is inflated (stdlib ``zlib``) before the
    search; one that does not inflate is searched as written."""
    for match in _PDF_STREAM.finditer(content):
        body = match.group("body")
        try:
            body = zlib.decompressobj().decompress(body)
        except zlib.error:
            pass
        if _PDF_TEXT_OPERATOR.search(body):
            return True
    return False


def _pdf_date_to_iso(raw: str) -> str | None:
    digits = raw[:8]
    if len(digits) < 8 or not digits.isdigit():
        return None
    year, month, day = int(digits[0:4]), int(digits[4:6]), int(digits[6:8])
    if not _valid_day(year, month, day):
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"

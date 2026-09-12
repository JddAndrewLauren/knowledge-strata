"""Raw unit (bytes, path) in; (paragraphs, title, metadata) out, or a refusal.

Converters are chosen by suffix and ported from memoria's ``normalize.py``
(github.com/JddAndrewLauren/memoria) with every date field stripped: no
converter here sets or reads a date, that is the dating module's job alone
(design.md, "Dating"). Plain text and markdown, docx (python-docx), pdf
(text layer only) and ``.eml`` are the whole claimed set; attachments and
container formats (mbox, pst, msg) are never opened. Paragraph text is
stored exactly as a converter produced it, since it is the anchor's match
key (ADR-0001) - nothing here reflows or normalizes whitespace away.

**Email.** A pre-pass deletes the bare ``Microsoft Mail Internet Headers``
line that defeats ``email.message_from_bytes`` (wayfinder #2). The ZL
attribution footer is cut before the quoted-reply splitter runs, which cuts
``> `` prefixes at any depth, Outlook's ``-----Original Message-----`` block
and interleaved quoting (the sender's own lines are kept in order because
each ``>`` line is dropped where it stands, not by cutting a whole tail).
From/To/Cc/Date/Subject become the record's first paragraph, verbatim,
because ``who`` is a text predicate and Record has no participants field.
``Thread-Index`` decodes into converter metadata for a later decision; it is
never a Record field.

**The binary-as-text guard.** A unit is indexed only if a converter claims
its suffix and returns text: decoded text carrying a NUL byte or a
non-trivial share of control characters is refused, and so is any paragraph
of several thousand characters with almost no whitespace. Refusals are a
typed result the sources adapter will count in its sync report.
"""

from __future__ import annotations

import base64
import binascii
import email
import re
from dataclasses import dataclass, field
from email.message import Message
from io import BytesIO
from pathlib import Path

from strata.record import first_sentence

# ZL's production wrote this bare line into the header block. It has no
# colon, so the standard library reads it as the header/body separator and
# silently swallows every header below it (wayfinder #2).
_BOGUS_HEADER_LINE = re.compile(rb"^Microsoft Mail Internet Headers Version [\d.]+\r?\n", re.MULTILINE)

# The producer's attribution footer, fenced by asterisk rules. Not the
# sender's words: cut before the quoted-reply split so neither it nor the
# paragraph splitter ever sees it.
_ZL_FOOTER = re.compile(
    r"\n?\*{5,}[ \t]*\nEDRM Enron Email Data Set has been produced .*?\n\*{5,}[ \t]*\n?",
    re.DOTALL,
)

# Outlook's own marker, introducing the "From:\nSent:\n..." block it quotes.
_ORIGINAL_MESSAGE_RE = re.compile(r"^-+\s*Original Message\s*-+\s*$", re.IGNORECASE)

_BLANK_LINE = re.compile(r"\n[ \t]*\n+")
_HEADING = re.compile(r"^#{1,6}\s+\S")

# Outlook's `Thread-Index`: the first 22 bytes identify the conversation and
# each reply appends exactly five more, so a reply's own bytes minus its
# last five are its parent's.
_THREAD_INDEX_ROOT_LEN = 22

# "Several thousand characters with almost no whitespace" (the guard).
_OVERSIZED_PARAGRAPH_MIN = 2000
_MIN_WHITESPACE_RATIO = 0.02
_MAX_CONTROL_CHAR_RATIO = 0.1


@dataclass(frozen=True)
class Conversion:
    """A converter's product: paragraphs in document order, an extractive
    title, and converter metadata for a later decision - never a Record
    field (``Thread-Index``'s conversation id and parent index, today)."""

    paragraphs: tuple[str, ...]
    title: str
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Refusal:
    """A raw unit no converter can index, and why."""

    reason: str


Result = Conversion | Refusal


def normalize(raw_bytes: bytes, path: str) -> Result:
    """Convert one raw unit by its suffix, or refuse it.

    ``path`` is used only to read the suffix; this module never touches the
    filesystem.
    """
    suffix = Path(path).suffix.lower()
    converter = _CONVERTERS.get(suffix)
    if converter is None:
        return Refusal(f"no converter claims the suffix {suffix!r}")
    return converter(raw_bytes)


# --- plain text and markdown ------------------------------------------------


def _decode_text(raw_bytes: bytes) -> str:
    """UTF-8, or cp1252 when the bytes are not UTF-8. Never raises: a unit
    that is not really text at all is a job for the guard below, not an
    exception."""
    try:
        return raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return raw_bytes.decode("cp1252", errors="replace")


def _split_text_paragraphs(text: str) -> list[str]:
    """Blank lines split paragraphs; a heading line is always its own
    paragraph, blank line or not."""
    paragraphs: list[str] = []
    current: list[str] = []

    def flush() -> None:
        if current:
            block = "\n".join(current).strip()
            if block:
                paragraphs.append(block)
            current.clear()

    for line in text.replace("\r\n", "\n").split("\n"):
        if not line.strip():
            flush()
            continue
        if _HEADING.match(line):
            flush()
            paragraphs.append(line.strip())
            continue
        current.append(line)
    flush()
    return paragraphs


def _convert_text(raw_bytes: bytes) -> Result:
    text = _decode_text(raw_bytes)
    paragraphs = _split_text_paragraphs(text)
    refusal = _guard(text, paragraphs)
    if refusal:
        return refusal
    heading = next((p for p in paragraphs if _HEADING.match(p)), None)
    title = heading.lstrip("#").strip() if heading else first_sentence(paragraphs)
    return Conversion(paragraphs=tuple(paragraphs), title=title)


# --- docx --------------------------------------------------------------------


def _is_heading_style(style_name: str) -> bool:
    style_name = style_name.lower()
    return style_name.startswith("heading") or style_name == "title"


def _convert_docx(raw_bytes: bytes) -> Result:
    import docx

    try:
        document = docx.Document(BytesIO(raw_bytes))
    except Exception as error:  # noqa: BLE001 - a bad docx is a refusal, not a crash
        return Refusal(f"not a readable docx: {error}")

    paragraphs = []
    heading = None
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        paragraphs.append(text)
        if heading is None and _is_heading_style(paragraph.style.name if paragraph.style else ""):
            heading = text

    refusal = _guard("\n\n".join(paragraphs), paragraphs)
    if refusal:
        return refusal
    title = heading if heading else first_sentence(paragraphs)
    return Conversion(paragraphs=tuple(paragraphs), title=title)


# --- pdf (text layer only) ----------------------------------------------------


def _convert_pdf(raw_bytes: bytes) -> Result:
    import pypdf

    try:
        reader = pypdf.PdfReader(BytesIO(raw_bytes))
    except Exception as error:  # noqa: BLE001 - a bad pdf is a refusal, not a crash
        return Refusal(f"not a readable pdf: {error}")

    paragraphs = []
    for page in reader.pages:
        text = (page.extract_text() or "").replace("\r\n", "\n")
        paragraphs.extend(block.strip() for block in _BLANK_LINE.split(text) if block.strip())

    if not paragraphs:
        # No text layer: reported by the caller, not indexed - not a refusal,
        # but the reason travels with it the same way a refusal's does.
        return Conversion(paragraphs=(), title="", metadata={"reason": "no text layer"})

    refusal = _guard("\n\n".join(paragraphs), paragraphs)
    if refusal:
        return refusal
    return Conversion(paragraphs=tuple(paragraphs), title=first_sentence(paragraphs))


# --- email ---------------------------------------------------------------------


def _header_paragraph(message: Message) -> str:
    """From, To, Cc, Date and Subject, verbatim, as the record's first
    paragraph: ``who`` is a text predicate and Record has no participants
    field, and display names are kept exactly as the header wrote them."""
    lines = [
        f"From: {message.get('From', '')}",
        f"To: {message.get('To', '')}",
        f"Cc: {message.get('Cc', '')}",
        f"Date: {message.get('Date', '')}",
        f"Subject: {message.get('Subject', '')}",
    ]
    return "\n".join(lines)


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        return raw if isinstance(raw, str) else ""
    charset = part.get_content_charset() or "latin-1"
    try:
        text = payload.decode(charset)
    except (LookupError, UnicodeDecodeError):
        text = payload.decode("latin-1", errors="replace")
    return text.replace("\r\n", "\n")


def _email_body_text(message: Message) -> str | None:
    """The plain-text body, or ``None`` when the message carries none. An
    attachment part (any part with a filename) is never opened."""
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get_filename() is not None:
                continue
            if part.get_content_type() == "text/plain":
                return _decode_part(part)
        return None
    if message.get_content_type() == "text/plain":
        return _decode_part(message)
    return None


def _is_outlook_header_start(lines: list[str], i: int) -> bool:
    """An Outlook "From:\\nSent:\\n..." block, identified by its first two
    lines so an ordinary "From:" in a reply's own prose is not mistaken for
    one."""
    return (
        i + 1 < len(lines)
        and re.match(r"^From:\s", lines[i]) is not None
        and re.match(r"^Sent:\s", lines[i + 1]) is not None
    )


def _cut_quoted_reply(body: str) -> str:
    """The three shapes the fixtures carry: ``> `` prefixes at any depth
    (dropped line by line, which is what keeps an interleaved reply's own
    lines in order), Outlook's ``-----Original Message-----`` block and the
    header block it introduces (a solid cut, everything after is quoted)."""
    lines = body.split("\n")
    kept = []
    for i, line in enumerate(lines):
        if _ORIGINAL_MESSAGE_RE.match(line.strip()) or _is_outlook_header_start(lines, i):
            break
        if line.startswith(">"):
            continue
        kept.append(line)
    return "\n".join(kept)


def _thread_metadata(message: Message) -> dict[str, str]:
    """``Thread-Index``, decoded into a conversation id (its first 22 bytes)
    and, for a reply, a parent index (its own bytes minus the last five) -
    converter metadata for a later decision, never a Record field."""
    raw = message.get("Thread-Index")
    if not raw:
        return {}
    stripped = raw.strip()
    try:
        index_bytes = base64.b64decode(stripped + "=" * (-len(stripped) % 4))
    except (binascii.Error, ValueError):
        return {}
    metadata = {"thread_index": index_bytes.hex()}
    if len(index_bytes) >= _THREAD_INDEX_ROOT_LEN:
        metadata["thread_conversation_id"] = index_bytes[:_THREAD_INDEX_ROOT_LEN].hex()
    if len(index_bytes) > _THREAD_INDEX_ROOT_LEN:
        metadata["thread_parent_index"] = index_bytes[:-5].hex()
    return metadata


def _convert_eml(raw_bytes: bytes) -> Result:
    repaired = _BOGUS_HEADER_LINE.sub(b"", raw_bytes, count=1)
    message = email.message_from_bytes(repaired)

    paragraphs = [_header_paragraph(message)]
    body_text = _email_body_text(message)
    if body_text is not None:
        clean_body = _cut_quoted_reply(_ZL_FOOTER.sub("\n", body_text))
        paragraphs.extend(block.strip() for block in _BLANK_LINE.split(clean_body) if block.strip())

    refusal = _guard("\n\n".join(paragraphs), paragraphs)
    if refusal:
        return refusal

    subject = (message.get("Subject") or "").strip()
    title = subject if subject else first_sentence(paragraphs)
    return Conversion(paragraphs=tuple(paragraphs), title=title, metadata=_thread_metadata(message))


# --- the binary-as-text guard --------------------------------------------------


def _is_control(character: str) -> bool:
    return (ord(character) < 0x20 and character not in "\n\t\r") or ord(character) == 0x7F


def _guard(text: str, paragraphs: list[str]) -> Refusal | None:
    if "\x00" in text:
        return Refusal("decoded text contains a NUL byte")
    if text:
        control_ratio = sum(1 for c in text if _is_control(c)) / len(text)
        if control_ratio > _MAX_CONTROL_CHAR_RATIO:
            return Refusal("decoded text contains a non-trivial share of control characters")
    for paragraph in paragraphs:
        if len(paragraph) < _OVERSIZED_PARAGRAPH_MIN:
            continue
        whitespace_ratio = sum(1 for c in paragraph if c.isspace()) / len(paragraph)
        if whitespace_ratio < _MIN_WHITESPACE_RATIO:
            return Refusal(f"a paragraph of {len(paragraph)} characters has almost no whitespace")
    return None


_CONVERTERS = {
    ".txt": _convert_text,
    ".md": _convert_text,
    ".docx": _convert_docx,
    ".pdf": _convert_pdf,
    ".eml": _convert_eml,
}

#!/usr/bin/env python3
"""Write the invented fixture sources of the west-desk demo project.

Dev tooling, never shipped. Everything here is invented: the people, the
company, the addresses (RFC 2606 ``example.com``) and every line of text. The
files are *structurally* faithful to the EDRM Enron export recorded on
wayfinder #2 - the bare ``Microsoft Mail Internet Headers`` line, CRLF,
quoted-printable, the ``X-ZL-*`` headers, the ZL attribution footer, a
``Thread-Index`` chain, one attachment - so the normalizer and the dating
module meet the defects they will meet on the real corpus. Nothing is copied
from that corpus, scrubbed or otherwise.

Two jobs:

    python scripts/make_fixtures.py            # (re)write examples/west-desk/sources/
    python scripts/make_fixtures.py --scale D  # write the large generated fixtures into D

The first is the record of what each committed file carries and the way to
regenerate it byte for byte (zip entries and pdf ids carry fixed timestamps,
so a rerun is a no-op diff). The second writes the fixtures that are too big
or too repetitive to commit - a day with more than 500 records, hundreds of
undated records, oversized paragraphs, semantic-only evidence - into a
directory a test owns. Tests import the ``scale_*`` functions directly instead
when they want the material in a ``tmp_path``. Expected assertions belong to
the acceptance gate (#22); this file only produces material.

Standard library only. The docx files are hand-built zips (the dating module
reads ``docProps/core.xml`` from the zip; python-docx opens them), the pdfs
are hand-built with an Info dictionary, so regenerating needs no install.
"""

from __future__ import annotations

import argparse
import base64
import io
import quopri
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "examples" / "west-desk" / "sources"

# --------------------------------------------------------------------------
# The cast. Invented, and shared with the manuscript and the notes so that
# `who` alias expansion, digests and the memoir all point at the same people.
# --------------------------------------------------------------------------

PEOPLE = {
    # key: (display "Last  First" as the export writes it, address, X.500 cn)
    "ruth": ("Kessler  Ruth", "Ruth.Kessler@example.com", "RKESSLER"),
    "marcus": ("Idowu  Marcus", "Marcus.Idowu@example.com", "MIDOWU"),
    "priya": ("Venkataraman  Priya", "Priya.Venkataraman@example.com", "PVENKATA"),
    "tomas": ("Lindqvist  Tomas", "Tomas.Lindqvist@example.com", "TLINDQVI"),
    "corinne": ("Baptiste  Corinne", "Corinne.Baptiste@example.com", "CBAPTIST"),
    "wes": ("Hargrove  Wes", "Wes.Hargrove@west.example.com", "WHARGROV"),
}

FOOTER = (
    "***********\n"
    "EDRM Enron Email Data Set has been produced in EML, PST and NSF format by ZL "
    "Technologies, Inc. This Data Set is licensed under a Creative Commons Attribution "
    "3.0 United States License <http://creativecommons.org/licenses/by/3.0/us/> . To "
    "provide attribution, please cite to \"ZL Technologies, Inc. (http://www.zlti.com).\"\n"
    "***********\n"
)

BOGUS_LINE = "Microsoft Mail Internet Headers Version 2.0"


def _addr(key: str) -> str:
    name, address, _ = PEOPLE[key]
    return f'"{name}" <{address}>'


def _x500(key: str) -> str:
    name, _, cn = PEOPLE[key]
    last, first = name.split("  ")
    return f"{last}, {first} </O=EXAMPLE/OU=NA/CN=RECIPIENTS/CN={cn}>"


def _crlf(text: str) -> bytes:
    return text.replace("\r\n", "\n").replace("\n", "\r\n").encode("iso-8859-1")


def _qp(text: str) -> str:
    """Quoted-printable body with the export's soft line breaks."""
    return quopri.encodestring(text.encode("iso-8859-1")).decode("ascii")


def eml(
    *,
    number: int,
    date: str | None,
    subject: str,
    sender: str,
    to: str,
    body: str,
    thread_index: bytes | None = None,
    received: str | None = None,
    x_zl: bool = True,
    folder: str = "\\Inbox",
) -> bytes:
    """One export-shaped message. The bogus line sits in the header block,
    above Subject/From/To, exactly where ZL's production put it."""
    lines = []
    if date is not None:
        lines.append(f"Date: {date}")
    if received is not None:
        lines.append(f"Received: {received}")
    lines += [
        f"Message-ID: <WESTDESK{number:04d}@NAHOU-MSMBX03V.corp.example.com>",
        "MIME-Version: 1.0",
        "Content-Type: text/plain; charset=ISO-8859-1",
        "Content-Transfer-Encoding: quoted-printable",
        BOGUS_LINE,
        "X-MimeOLE:  Produced By Microsoft Exchange V6.0.4712.0",
        "content-class:  urn:content-classes:message",
        f"Subject:  {subject}",
        f"Thread-Topic:  {subject.split(': ', 1)[-1]}",
    ]
    if thread_index is not None:
        lines.append(f"Thread-Index:  {base64.b64encode(thread_index).decode('ascii')}")
    lines += [f"From:  {sender}", f"To:  {to}"]
    if x_zl:
        lines.append(f"X-ZL-From:  {_x500(_key_of(sender))}")
    lines += [f"X-Folder:  {folder}", "X-ZLID:  zl-edrm-enron-v2-fixture.eml", ""]
    text = body.rstrip("\n") + "\n\n" + FOOTER if body.strip() else FOOTER
    return _crlf("\n".join(lines) + "\n" + _qp(text))


def _key_of(sender: str) -> str:
    for key, (_, address, _) in PEOPLE.items():
        if address in sender:
            return key
    return "ruth"


def attachment_eml(*, number: int, date: str, subject: str, sender: str, to: str, body: str) -> bytes:
    boundary = "----_=_NextPart_001_01C0E3A2.7A1B44C0"
    fake_xls = base64.b64encode(b"fixture spreadsheet, not a real workbook\n").decode("ascii")
    text = "\n".join(
        [
            f"Date: {date}",
            f"Message-ID: <WESTDESK{number:04d}@NAHOU-MSMBX03V.corp.example.com>",
            "MIME-Version: 1.0",
            f'Content-Type: multipart/mixed; boundary="{boundary}"',
            BOGUS_LINE,
            "content-class:  urn:content-classes:message",
            f"Subject:  {subject}",
            f"From:  {sender}",
            f"To:  {to}",
            "X-ZLID:  zl-edrm-enron-v2-fixture.eml",
            "",
            f"--{boundary}",
            "Content-Type: text/plain; charset=ISO-8859-1",
            "Content-Transfer-Encoding: 7bit",
            "",
            body.rstrip("\n"),
            "",
            FOOTER.rstrip("\n"),
            "",
            f"--{boundary}",
            'Content-Type: application/vnd.ms-excel; name="cascade-tie-load.xls"',
            "Content-Transfer-Encoding: base64",
            'Content-Disposition: attachment; filename="cascade-tie-load.xls"',
            "",
            fake_xls,
            "",
            f"--{boundary}--",
            "",
        ]
    )
    return _crlf(text)


# Thread-Index: 22 bytes for the conversation, five more per reply.
THREAD_ROOT = bytes.fromhex("01c0dd4a3b8f2e5c91a0b7d6e4f3021a7c5d")  # 18 bytes
THREAD_PARENT = b"\x01" + THREAD_ROOT + b"\x00\x00\x00"  # 22 bytes
THREAD_CHILD = THREAD_PARENT + b"\x00\x1a\x2b\x3c\x4d"  # 27 bytes


def mail_fixtures() -> dict[str, bytes]:
    r, m, p, t, c, w = (_addr(k) for k in ("ruth", "marcus", "priya", "tomas", "corinne", "wes"))
    desk = f"{r}, {t}, {p}"
    return {
        # The plain case: one bogus line, X-ZL headers, footer, two paragraphs.
        "plain.eml": eml(
            number=1,
            date="Wed, 23 May 2001 08:58:33 -0500 (CDT)",
            subject="Cascade tie congestion report",
            sender=p,
            to=r,
            body=(
                "The congestion working group meets on the 30th at ten. Bring the "
                "hourly figures for the Cascade tie, not the daily roll-up.\n\n"
                "I will send the load figures before then; the auction results are "
                "not final until Friday."
            ),
        ),
        # Nested `> > > ` quoting, the sender's line on top.
        "quoted-angle.eml": eml(
            number=2,
            date="Tue, 5 Jun 2001 16:02:11 -0500 (CDT)",
            subject="RE: Friday schedule",
            sender=t,
            to=r,
            body=(
                "Agreed, moving it. Marcus signed off on the call.\n\n"
                "> Can we push the Friday schedule to after the Portland call?\n"
                "> > Wes wants the whole desk on the line at nine.\n"
                "> > > Portland call is nine Central, not nine Pacific."
            ),
        ),
        # Outlook's `-----Original Message-----` block.
        "outlook-original-message.eml": eml(
            number=3,
            date="Wed, 20 Jun 2001 07:41:09 -0500 (CDT)",
            subject="RE: Cascade tie outage",
            sender=m,
            to=desk,
            body=(
                "Nobody schedules across the tie until the operator lifts the "
                "derate. Priya has the numbers; Tomas, hold the noon submission.\n\n"
                "-----Original Message-----\n"
                "From: Venkataraman, Priya\n"
                "Sent: Tuesday, June 19, 2001 11:52 PM\n"
                "To: Idowu, Marcus\n"
                "Subject: Cascade tie outage\n\n"
                "The operator posted a forced outage on the Cascade tie at 23:10. "
                "Derate to 40 percent through at least Thursday."
            ),
        ),
        # Interleaved: a quoted line, the answer, then quoting again.
        "interleaved.eml": eml(
            number=4,
            date="Thu, 21 Jun 2001 09:15:47 -0500 (CDT)",
            subject="RE: Cascade tie outage",
            sender=p,
            to=m,
            body=(
                "> Is the derate fixed at 40 for the whole outage?\n\n"
                "No, it steps. Forty today, sixty on Thursday if the second line holds.\n\n"
                "> And the noon submission?\n\n"
                "Tomas is holding it. I told him to wait for your word."
            ),
        ),
        # A conversation the Thread-Index makes recoverable: parent and reply.
        "thread-parent.eml": eml(
            number=5,
            date="Wed, 16 May 2001 14:20:05 -0500 (CDT)",
            subject="Scheduling cutoff",
            sender=m,
            to=desk,
            body=(
                "Starting Monday the scheduling cutoff for the west desk moves to "
                "eleven. Noon submissions will be refused by the operator."
            ),
            thread_index=THREAD_PARENT,
        ),
        "thread-child.eml": eml(
            number=6,
            date="Thu, 17 May 2001 08:03:52 -0500 (CDT)",
            subject="RE: Scheduling cutoff",
            sender=t,
            to=m,
            body=(
                "Understood. I have moved the desk checklist to ten-thirty so the "
                "eleven cutoff has half an hour of slack."
            ),
            thread_index=THREAD_CHILD,
        ),
        # Multipart with one named attachment. Nothing opens it.
        "attachment.eml": attachment_eml(
            number=7,
            date="Wed, 23 May 2001 09:14:02 -0500 (CDT)",
            subject="Cascade tie load figures",
            sender=p,
            to=r,
            body="Load figures attached, hourly, for the working group on the 30th.",
        ),
        # Long paragraphs so quoted-printable wraps them with soft breaks.
        "quoted-printable-wrapped.eml": eml(
            number=8,
            date="Thu, 8 Nov 2001 10:12:05 -0600 (CST)",
            subject="Settlement dispute, October",
            sender=c,
            to=f"{m}, {r}",
            body=(
                "The October settlement statement disagrees with our scheduled "
                "volumes on the Cascade tie for the outage week by roughly eleven "
                "thousand megawatt hours, all of it on the days the derate stepped, "
                "and I believe the operator settled us at the sixty percent figure "
                "on the days we were told forty.\n\n"
                "I have asked for the hourly settlement detail; expect it Friday and "
                "do not sign the statement before then."
            ),
        ),
        # Display names only, no addresses: half the corpus is written this way.
        "display-names-only.eml": eml(
            number=9,
            date="Mon, 14 Jan 2002 07:30:00 -0600 (CST)",
            subject="Desk coverage this week",
            sender=m,
            to="Kessler, Ruth; Lindqvist, Tomas; Baptiste, Corinne; PV",
            body=(
                "Ruth takes real-time Monday and Tuesday. PV is out until Thursday. "
                "Tomas covers scheduling all week."
            ),
            x_zl=False,
        ),
        # No Date header at all: the earliest Received header dates it, inferred.
        "received-only.eml": eml(
            number=10,
            date=None,
            received=(
                "from NAHOU-MSMBX03V.corp.example.com ([10.0.0.12]) by "
                "nahou-mscnx06p.corp.example.com with Microsoft SMTPSVC(5.0.2195.2966); "
                "Tue, 4 Sep 2001 15:44:10 -0500"
            ),
            subject="Portland trip",
            sender=w,
            to=r,
            body="Booked for the 18th. Bring the tie figures; they will ask.",
            folder="\\Sent Items",
        ),
        # Empty body once the footer is cut: 665 corpus messages look like this.
        "empty-body.eml": eml(
            number=11,
            date="Fri, 30 Nov 2001 17:05:30 -0600 (CST)",
            subject="",
            sender=t,
            to=r,
            body="",
        ),
    }


# --------------------------------------------------------------------------
# Plain-text sources for the path and first-line rungs.
# --------------------------------------------------------------------------

TEXT_FIXTURES = {
    # Folder date only, month granularity: 2001/May/. No date in the text.
    "2001/May/desk-checklist.txt": (
        "West desk morning checklist\n\n"
        "Pull the operator's overnight postings before anything else.\n"
        "Check the Cascade tie derate line, then the day-ahead awards.\n"
        "Checklist closes at ten-thirty; the cutoff is eleven.\n"
    ),
    # Folder date, month granularity, a different month.
    "2001/November/settlement-notes.txt": (
        "Settlement notes, west desk\n\n"
        "Corinne's dispute is with the outage week. The operator settled at sixty on "
        "days we scheduled at forty. Hourly detail requested; statement unsigned.\n"
    ),
    # Year-only folder.
    "2002/desk-reorganisation.txt": (
        "Desk reorganisation\n\n"
        "Scheduling and real-time merge under Tomas. Ruth moves to the book.\n"
    ),
    # ISO date in the filename, exact, day granularity.
    "memos/2002-01-14-scheduling-change.txt": (
        "Scheduling change memo\n\n"
        "From Monday the west desk submits one combined schedule. Marcus has told "
        "the operator; Wes has told Portland.\n"
    ),
    # First line is a bare date: exact, day granularity, beats the folder date.
    "2001/June/journal-outage-week.txt": (
        "19 June 2001\n\n"
        "The tie went out at ten past eleven at night. Priya called Marcus at home; "
        "Marcus called nobody, and in the morning said nobody schedules across it.\n\n"
        "Tomas held the noon submission. First time I had seen him hold anything.\n"
    ),
    # A first line with a labelled date inside a longer heading: inferred.
    "memos/portland-call.txt": (
        "Notes from the Portland call, 4 June 2001\n\n"
        "Wes wants the whole desk on the line at nine Central. Friday schedule moves "
        "to after the call. Marcus signed off.\n"
    ),
    # All-numeric, both fields 12 or under: year only, day/month ambiguous.
    "memos/tie-figures.txt": (
        "11/4/2001\n\n"
        "Hourly tie figures for the working group. Forty percent derate days marked.\n"
    ),
    # Nothing to date it by: no header, no first-line date, no path date.
    "memos/glossary.txt": (
        "Desk glossary\n\n"
        "Derate: a reduction of the tie's rated capacity posted by the operator.\n"
        "Cutoff: the hour after which the operator refuses a schedule.\n"
        "The book: the position ledger Ruth keeps after the reorganisation.\n"
    ),
    # Not text at all, named as text: the binary-as-text guard (#10 -> normalizer).
    "memos/not-really-text.txt": bytes(range(256)) * 4,
}


# --------------------------------------------------------------------------
# docx and pdf with set core properties, hand-built.
# --------------------------------------------------------------------------

def docx(*, title: str, creator: str, created: str, modified: str, paragraphs: list[str]) -> bytes:
    """A minimal Word document. ``created``/``modified`` are W3CDTF strings."""
    def esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    body = "".join(f"<w:p><w:r><w:t xml:space=\"preserve\">{esc(p)}</w:t></w:r></w:p>" for p in paragraphs)
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
            "</Relationships>"
        ),
        "docProps/core.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            f"<dc:title>{esc(title)}</dc:title><dc:creator>{esc(creator)}</dc:creator>"
            f'<dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>'
            f'<dcterms:modified xsi:type="dcterms:W3CDTF">{modified}</dcterms:modified>'
            "</cp:coreProperties>"
        ),
        "word/document.xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body>{body}</w:body></w:document>"
        ),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for name, xml in parts.items():
            info = zipfile.ZipInfo(name, date_time=(2001, 1, 1, 0, 0, 0))
            z.writestr(info, xml.encode("utf-8"))
    return buf.getvalue()


def pdf(*, title: str, creator: str, producer: str, creation_date: str, text: str | None) -> bytes:
    """A one-page pdf with an Info dictionary. ``text=None`` gives an empty
    page - no text layer, the scanner case."""
    def obj(n: int, body: str) -> bytes:
        return f"{n} 0 obj\n{body}\nendobj\n".encode("latin-1")

    if text is None:
        stream = b""
    else:
        stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objects = [
        obj(1, "<< /Type /Catalog /Pages 2 0 R >>"),
        obj(2, "<< /Type /Pages /Kids [3 0 R] /Count 1 >>"),
        obj(3, "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
               "/Resources << /Font << /F1 5 0 R >> >> >>"),
        b"4 0 obj\n<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream\nendobj\n",
        obj(5, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"),
        obj(6, f"<< /Title ({title}) /Creator ({creator}) /Producer ({producer}) "
               f"/CreationDate ({creation_date}) >>"),
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for o in objects:
        offsets.append(len(out))
        out += o
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R /Info 6 0 R >>\n"
            f"startxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)


def binary_fixtures() -> dict[str, bytes]:
    return {
        # Genuine creation date: inferred, "docx created 2001-08-20".
        "memos/desk-procedures.docx": docx(
            title="West desk procedures",
            creator="Ruth Kessler",
            created="2001-08-20T14:05:00Z",
            modified="2001-08-22T09:30:00Z",
            paragraphs=[
                "West desk procedures",
                "The checklist closes at ten-thirty and the schedule goes in by eleven.",
                "During a posted derate nobody schedules across the Cascade tie without the desk head's word.",
            ],
        ),
        # A template date: created years before the text was written. Still
        # inferred - the dating module cannot tell - and the first-real-corpus
        # failure the docx rung warns about.
        "memos/outage-letter.docx": docx(
            title="Letter",
            creator="Meridian Templates",
            created="1998-03-15T10:00:00Z",
            modified="2001-06-25T16:45:00Z",
            paragraphs=[
                "To the operator, concerning the forced outage on the Cascade tie.",
                "We scheduled at forty percent on the days the derate was posted at forty and ask that settlement reflect it.",
            ],
        ),
        # pdf Info CreationDate, with a text layer: inferred 2002-02-05.
        "memos/outage-notice.pdf": pdf(
            title="Planned outage notice",
            creator="Ruth Kessler",
            producer="Meridian Print Service 1.2",
            creation_date="D:20020205101500-06'00'",
            text="Planned outage, Cascade tie, 12 to 14 February 2002. Derate to fifty percent.",
        ),
        # A scan: Creator names a scanner and there is no text layer: unknown.
        "memos/signed-statement-scan.pdf": pdf(
            title="",
            creator="HP ScanJet 5p",
            producer="ScanSoft PaperPort 6.1",
            creation_date="D:20011203091200-06'00'",
            text=None,
        ),
    }


# --------------------------------------------------------------------------
# Scale fixtures: generated, never committed. Deterministic by construction.
# --------------------------------------------------------------------------

def _hour(i: int) -> str:
    return f"{i // 60 % 24:02d}:{i % 60:02d}:00"


def scale_busy_day(root: Path, n: int = 520, day: str = "Thu, 17 May 2001") -> list[Path]:
    """More than 500 source records on one calendar day (acceptance #22 test 1).
    Every body is distinct; every message is a lexical hit for ``cutoff``."""
    out = []
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        path = root / f"busy-{i:04d}.eml"
        path.write_bytes(
            eml(
                number=1000 + i,
                date=f"{day} {_hour(i)} -0500 (CDT)",
                subject=f"Cutoff note {i}",
                sender=_addr("tomas"),
                to=_addr("ruth"),
                body=f"Cutoff note number {i}: the eleven o'clock cutoff held on submission {i}.",
                thread_index=None,
            )
        )
        out.append(path)
    return out


def scale_undated(root: Path, n: int = 520) -> list[Path]:
    """More than 500 records with no date anywhere: no header, no first line,
    no path pattern. Each repeats the same evidence phrase so they recur across
    scopes the way unknown-dated records do."""
    out = []
    folder = root / "undated"
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        path = folder / f"note-{i:04d}.txt"
        path.write_text(
            f"Desk note {i}\n\nThe derate line on the Cascade tie is checked before the awards. Item {i}.\n",
            encoding="utf-8",
        )
        out.append(path)
    return out


def scale_semantic_only(root: Path, n: int = 12) -> list[Path]:
    """Records that never contain the query word ``outage`` but describe one,
    for a dict-backed fake embedder to place next to it. The test wires the
    embedder; these only guarantee the lexical count is zero."""
    out = []
    folder = root / "semantic"
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        path = folder / f"2001-06-{19 + i % 5:02d}-tie-{i:02d}.txt"
        path.write_text(
            f"Tie status {i}\n\nThe interconnection tripped and the operator cut the rating; "
            f"schedules across it stopped until the line came back. Entry {i}.\n",
            encoding="utf-8",
        )
        out.append(path)
    return out


def oversized_paragraph(chars: int = 40_000) -> str:
    """One paragraph past the ~8k-token reply cap, with multibyte, combining,
    tab and repeated-whitespace content so exact reconstruction is tested."""
    unit = "Deraté held at forty per cent—schedule\tnothing across the tie;  wait.  éè 日本 "
    reps = chars // len(unit) + 1
    return (unit * reps)[:chars]


def scale_oversized(root: Path) -> dict[str, Path]:
    """An oversized source paragraph, an oversized note and an oversized
    manuscript section, in the shapes their adapters read."""
    root.mkdir(parents=True, exist_ok=True)
    big = oversized_paragraph()
    paths = {
        "source": root / "sources" / "2001" / "July" / "oversized.txt",
        "note": root / "notes" / "theme" / "oversized.md",
        "manuscript": root / "manuscript" / "ch99-oversized.md",
    }
    paths["source"].parent.mkdir(parents=True, exist_ok=True)
    paths["source"].write_text("Oversized memo\n\n" + big + "\n\nA short closing paragraph.\n", encoding="utf-8")
    paths["note"].parent.mkdir(parents=True, exist_ok=True)
    paths["note"].write_text("---\naliases: [oversized]\n---\n# Oversized theme\n\n- 2001-07-02  " + big + "\n", encoding="utf-8")
    paths["manuscript"].parent.mkdir(parents=True, exist_ok=True)
    paths["manuscript"].write_text("# July 2001\n\n## The long week\n\n" + big + "\n\n## After\n\nShort.\n", encoding="utf-8")
    return paths


def paragraph_versions() -> dict[str, list[str]]:
    """Two versions of one record's paragraph list, for the ledger's exact
    anchor rule (acceptance #22 test 6): kept text keeps its anchor; cosmetic
    and substantive edits retire; duplicates pair in document order; a moved
    paragraph keeps its anchor; a deleted one retires."""
    v1 = [
        "The tie went out at ten past eleven at night.",   # p1 kept
        "Tomas held the noon submission.",                   # p2 cosmetic edit -> retired
        "Nobody schedules across it.",                       # p3 duplicate A
        "Nobody schedules across it.",                       # p4 duplicate B
        "Priya called Marcus at home.",                      # p5 deleted -> retired
        "Derate to forty percent.",                          # p6 moved, kept
        "Café on the corner.",                          # p7 NFC; v2 has NFD -> retired
    ]
    v2 = [
        "Derate to forty percent.",                          # p6 (moved)
        "The tie went out at ten past eleven at night.",     # p1
        "Tomas held the noon submission",                    # punctuation dropped -> new anchor
        "Nobody schedules across it.",                       # pairs with p3 in document order
        "Café on the corner.",                         # NFD -> new anchor
        "Nobody schedules across it.",                       # pairs with p4
        "A new closing paragraph.",                          # added
    ]
    return {"v1": v1, "v2": v2}


def scale_all(root: Path) -> None:
    scale_busy_day(root / "sources" / "busy")
    scale_undated(root / "sources")
    scale_semantic_only(root / "sources")
    scale_oversized(root)
    (root / "paragraph-versions.txt").write_text(
        "\n".join(f"{k}: {v!r}" for k, v in paragraph_versions().items()) + "\n", encoding="utf-8"
    )


# --------------------------------------------------------------------------

def write_committed(sources: Path = SOURCES) -> list[Path]:
    if sources.exists():
        shutil.rmtree(sources)
    written = []
    for name, data in mail_fixtures().items():
        written.append(_write(sources / "mail" / name, data))
    for name, data in TEXT_FIXTURES.items():
        written.append(_write(sources / name, data if isinstance(data, bytes) else data.encode("utf-8")))
    for name, data in binary_fixtures().items():
        written.append(_write(sources / name, data))
    return written


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--scale", metavar="DIR", type=Path,
                        help="write the generated scale fixtures into DIR instead of the committed set")
    args = parser.parse_args(argv)
    if args.scale:
        scale_all(args.scale)
        print(f"scale fixtures written under {args.scale}")
        return 0
    for path in write_committed():
        print(path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())

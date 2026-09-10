"""The west-desk fixture contract: each file still exhibits the defect it is
named for, and nothing in it names a real person or domain.

Ported from memoria's ``tests/test_enron_fixtures.py`` (wayfinder #2, #12).
The fixtures are structurally faithful to the EDRM Enron export and textually
invented; ``scripts/make_fixtures.py`` writes them and is the record of what
each carries. Nothing converts them here - the normalizer and dating build
issues own that. This file keeps a well-meaning tidy-up from quietly removing
the defect that makes a fixture worth keeping.
"""

import base64
import email
import re
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "examples" / "west-desk" / "sources"
MAIL = SOURCES / "mail"

sys.path.insert(0, str(ROOT / "scripts"))
import make_fixtures  # noqa: E402

# ZL's production wrote this bare line into the header block. It has no colon,
# so the standard library reads it as the header/body separator and silently
# swallows every header below it (wayfinder #2, consequence 2).
BOGUS_HEADER = re.compile(rb"^Microsoft Mail Internet Headers Version [\d.]+\r?\n", re.MULTILINE)

FOOTERED = (
    "plain", "quoted-angle", "outlook-original-message", "interleaved",
    "thread-parent", "thread-child", "attachment", "quoted-printable-wrapped",
    "display-names-only", "received-only", "empty-body",
)


def _read(name):
    return (MAIL / f"{name}.eml").read_bytes()


def _repaired(name):
    return email.message_from_bytes(BOGUS_HEADER.sub(b"", _read(name), count=1))


def _body(message):
    if not message.is_multipart():
        part = message
    else:
        part = next(p for p in message.walk() if p.get_content_type() == "text/plain")
    return part.get_payload(decode=True).decode(part.get_content_charset() or "latin-1")


def _without_footer(text):
    return text.split("***********")[0]


def test_the_generator_reproduces_the_committed_files_byte_for_byte(tmp_path):
    """Regeneration is a no-op diff: the script is the record of each file."""
    make_fixtures.write_committed(tmp_path)
    committed = {p.relative_to(SOURCES): p.read_bytes() for p in SOURCES.rglob("*") if p.is_file()}
    generated = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert committed == generated


def test_every_mail_fixture_uses_crlf_as_the_corpus_does():
    for path in sorted(MAIL.glob("*.eml")):
        raw = path.read_bytes()
        assert b"\r\n" in raw, path.name
        assert not re.search(rb"(?<!\r)\n", raw), f"{path.name} has a bare LF"


def test_the_stray_header_line_defeats_the_standard_library():
    """The finding the normalizer exists to survive, pinned as a test."""
    message = email.message_from_bytes(_read("plain"))

    assert message.defects, "the fixture no longer reproduces the defect"
    assert message.get("From") is None
    assert message.get("Subject") is None


def test_removing_that_one_line_recovers_every_header():
    message = _repaired("plain")

    assert not message.defects
    assert message.get("Subject").strip() == "Cascade tie congestion report"
    assert "Priya.Venkataraman@example.com" in message.get("From")
    assert message.get("X-ZL-From").strip().startswith("Venkataraman, Priya </O=EXAMPLE/")


def test_every_body_carries_the_zl_attribution_footer():
    """Not the sender's words: the normalizer cuts it before the splitter."""
    for name in FOOTERED:
        assert "EDRM Enron Email Data Set has been produced" in _body(_repaired(name)), name


def test_the_empty_body_fixture_is_empty_once_the_footer_is_cut():
    """665 corpus messages have neither subject nor body after the cut."""
    message = _repaired("empty-body")
    assert message.get("Subject").strip() == ""
    assert _without_footer(_body(message)).strip() == ""


def test_no_fixture_carries_a_reply_header():
    """`In-Reply-To` is on 8 of 4,302 corpus messages; `Thread-Index` is the substitute."""
    for path in sorted(MAIL.glob("*.eml")):
        message = email.message_from_bytes(BOGUS_HEADER.sub(b"", path.read_bytes(), count=1))
        assert message.get("In-Reply-To") is None, path.name
        assert message.get("References") is None, path.name


def test_thread_index_makes_the_parent_recoverable():
    """A reply appends five bytes to its parent's 22-byte index."""
    def index(name):
        raw = _repaired(name).get("Thread-Index").strip()
        return base64.b64decode(raw + "=" * (-len(raw) % 4))

    parent, child = index("thread-parent"), index("thread-child")

    assert len(parent) == 22
    assert len(child) == 27
    assert child[:-5] == parent


def test_each_quoting_style_the_cut_rules_name_is_represented():
    assert re.search(r"^> > > ", _body(_repaired("quoted-angle")), re.MULTILINE)

    outlook = _body(_repaired("outlook-original-message"))
    assert "-----Original Message-----" in outlook
    assert re.search(r"^Sent: ", outlook, re.MULTILINE)

    # Interleaved: quoted line, the sender's answer, then quoting again. The
    # splitter keeps the sender's lines in order rather than cutting at the
    # first marker.
    interleaved = _body(_repaired("interleaved"))
    assert len(re.findall(r"^> ", interleaved, re.MULTILINE)) == 2
    assert "No, it steps." in interleaved


def test_quoted_printable_soft_breaks_split_a_paragraph_mid_word():
    raw = _read("quoted-printable-wrapped")
    assert re.search(rb"[a-z]=\r\n[a-z]", raw), "no soft break inside a word"
    body = _without_footer(_body(_repaired("quoted-printable-wrapped")))
    assert "eleven thousand megawatt hours" in body.replace("\r\n", " ")


def test_the_attachment_fixture_is_multipart_with_a_named_file():
    message = _repaired("attachment")

    assert message.is_multipart()
    names = [part.get_filename() for part in message.walk() if part.get_filename()]
    assert names == ["cascade-tie-load.xls"]


def test_display_names_only_has_no_address_in_to():
    """Half the corpus writes correspondents as `Last, First` with no address."""
    to = _repaired("display-names-only").get("To")
    assert "@" not in to
    assert "Kessler, Ruth" in to and "PV" in to


def test_received_only_has_no_date_header_but_a_received_one():
    """The `Received` rung: inferred, from the earliest Received header."""
    message = _repaired("received-only")
    assert message.get("Date") is None
    assert "Tue, 4 Sep 2001 15:44:10 -0500" in message.get("Received")


def test_the_fixtures_name_no_real_person_or_domain():
    """RFC 2606 reserved domain, invented people. Nothing to scrub."""
    for path in sorted(MAIL.glob("*.eml")):
        text = path.read_bytes().decode("iso-8859-1")
        assert "@enron.com" not in text.lower(), path.name
        for address in re.findall(r"[\w.+-]+@[\w.-]+", text):
            if address.endswith((".xls", ".eml")):
                continue
            assert address.endswith(("example.com", "corp.example.com", "west.example.com")), (path.name, address)


# --- the path, first-line and metadata rungs -------------------------------

@pytest.mark.parametrize(
    "relative, rung",
    [
        ("2001/May/desk-checklist.txt", "folder month"),
        ("2001/November/settlement-notes.txt", "folder month"),
        ("2002/desk-reorganisation.txt", "folder year"),
        ("memos/2002-01-14-scheduling-change.txt", "filename ISO day"),
    ],
)
def test_path_dated_texts_carry_no_date_in_their_text(relative, rung):
    """Only the path can date these; the text must not pre-empt the rung."""
    text = (SOURCES / relative).read_text(encoding="utf-8")
    assert not re.search(r"\b(19|20)\d\d\b", text), (relative, rung)


def test_first_line_dates_are_where_the_dating_rules_look():
    exact = (SOURCES / "2001/June/journal-outage-week.txt").read_text(encoding="utf-8").splitlines()[0]
    assert exact == "19 June 2001"
    inside = (SOURCES / "memos/portland-call.txt").read_text(encoding="utf-8").splitlines()[0]
    assert "4 June 2001" in inside and len(inside) > 20
    ambiguous = (SOURCES / "memos/tie-figures.txt").read_text(encoding="utf-8").splitlines()[0]
    assert ambiguous == "11/4/2001"


def test_the_glossary_has_nothing_to_date_it_by():
    text = (SOURCES / "memos/glossary.txt").read_text(encoding="utf-8")
    assert not re.search(r"\d", text)


def test_the_binary_as_text_guard_fixture_is_not_text():
    raw = (SOURCES / "memos/not-really-text.txt").read_bytes()
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    assert b"\x00" in raw


def test_docx_core_properties_carry_the_dates_the_metadata_rung_reads():
    def created(name):
        core = zipfile.ZipFile(SOURCES / "memos" / name).read("docProps/core.xml").decode()
        return re.search(r"<dcterms:created[^>]*>([^<]+)<", core).group(1)

    assert created("desk-procedures.docx").startswith("2001-08-20")
    # The template date: created years before the text, still `inferred`.
    assert created("outage-letter.docx").startswith("1998-03-15")


def test_pdf_info_dictionaries_distinguish_a_document_from_a_scan():
    notice = (SOURCES / "memos/outage-notice.pdf").read_bytes()
    assert b"/CreationDate (D:20020205" in notice
    assert b"Tj" in notice, "the notice has a text layer"

    scan = (SOURCES / "memos/signed-statement-scan.pdf").read_bytes()
    assert b"/Creator (HP ScanJet" in scan
    assert b"Tj" not in scan, "the scan has no text layer"


# --- scale fixtures: generated, never committed ---------------------------

def test_scale_generators_are_deterministic_and_cross_the_stated_limits(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    make_fixtures.scale_all(a)
    make_fixtures.scale_all(b)
    snapshot = lambda root: {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}  # noqa: E731
    assert snapshot(a) == snapshot(b)

    assert len(list((a / "sources" / "busy").glob("*.eml"))) > 500
    assert len(list((a / "sources" / "undated").glob("*.txt"))) > 500
    for path in (a / "sources" / "semantic").glob("*.txt"):
        assert "outage" not in path.read_text(encoding="utf-8").lower()
    assert len(make_fixtures.oversized_paragraph()) * 0.25 > 8_000, "past the ~8k-token reply cap"

    versions = make_fixtures.paragraph_versions()
    assert versions["v1"].count("Nobody schedules across it.") == 2
    assert "Priya called Marcus at home." not in versions["v2"]
    assert versions["v1"][6] != versions["v2"][4], "NFC and NFD are different match keys"


def test_nothing_under_the_demo_project_is_a_generated_scale_fixture():
    assert not (SOURCES / "busy").exists()
    assert not (SOURCES / "undated").exists()
    assert sum(1 for _ in SOURCES.rglob("*") if _.is_file()) < 40

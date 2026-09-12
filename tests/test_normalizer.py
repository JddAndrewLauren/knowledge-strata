"""The normalizer's converters, the email repairs and the binary-as-text
guard (wayfinder #28). ``tests/test_west_desk_fixtures.py`` pins the
fixtures themselves; this file converts them and checks the result.
"""

from pathlib import Path

import pytest

from strata.normalizer import Conversion, Refusal, normalize

ROOT = Path(__file__).resolve().parent.parent
SOURCES = ROOT / "examples" / "west-desk" / "sources"
MAIL = SOURCES / "mail"


def _convert(relative: str) -> Conversion:
    path = SOURCES / relative
    result = normalize(path.read_bytes(), relative)
    assert isinstance(result, Conversion), f"{relative}: refused, {result}"
    return result


# --- email: header recovery, footer, quoting -------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "plain",
        "quoted-angle",
        "outlook-original-message",
        "interleaved",
        "thread-parent",
        "thread-child",
        "attachment",
        "quoted-printable-wrapped",
        "display-names-only",
        "received-only",
        "empty-body",
    ],
)
def test_every_mail_fixture_converts_with_no_footer_in_any_paragraph(name):
    conversion = _convert(f"mail/{name}.eml")
    for paragraph in conversion.paragraphs:
        assert "EDRM Enron Email Data Set has been produced" not in paragraph


def test_the_first_paragraph_is_the_header_block_as_text():
    conversion = _convert("mail/display-names-only.eml")
    header = conversion.paragraphs[0]
    assert header.startswith("From: ")
    assert "Kessler, Ruth; Lindqvist, Tomas; Baptiste, Corinne; PV" in header


def test_quoted_angle_drops_every_depth_of_quoting():
    conversion = _convert("mail/quoted-angle.eml")
    body = "\n".join(conversion.paragraphs[1:])
    assert "Agreed, moving it. Marcus signed off on the call." in body
    assert ">" not in body


def test_outlook_original_message_block_is_cut_solid():
    conversion = _convert("mail/outlook-original-message.eml")
    body = "\n".join(conversion.paragraphs[1:])
    assert "Nobody schedules across the tie" in body
    assert "Original Message" not in body
    assert "Sent:" not in body
    assert "forced outage on the Cascade tie" not in body  # the quoted reply


def test_interleaved_keeps_the_senders_lines_in_order():
    conversion = _convert("mail/interleaved.eml")
    body = conversion.paragraphs[1:]
    assert body == (
        "No, it steps. Forty today, sixty on Thursday if the second line holds.",
        "Tomas is holding it. I told him to wait for your word.",
    )


def test_quoted_printable_soft_breaks_join_into_one_run():
    conversion = _convert("mail/quoted-printable-wrapped.eml")
    body = "\n".join(conversion.paragraphs)
    assert "eleven thousand megawatt hours" in body


def test_the_attachment_is_never_opened():
    conversion = _convert("mail/attachment.eml")
    for paragraph in conversion.paragraphs:
        assert "cascade-tie-load.xls" not in paragraph
    assert any("Load figures attached" in p for p in conversion.paragraphs)


def test_thread_index_decodes_to_a_shared_conversation_id_with_the_parent_index_chained():
    parent = _convert("mail/thread-parent.eml")
    child = _convert("mail/thread-child.eml")

    assert parent.metadata["thread_conversation_id"] == child.metadata["thread_conversation_id"]
    assert child.metadata["thread_parent_index"] == parent.metadata["thread_index"]


def test_empty_body_has_the_header_paragraph_only_and_a_nonempty_title():
    conversion = _convert("mail/empty-body.eml")
    assert len(conversion.paragraphs) == 1
    assert conversion.title


def test_plain_email_title_is_the_subject():
    conversion = _convert("mail/plain.eml")
    assert conversion.title == "Cascade tie congestion report"


# --- the binary-as-text guard ------------------------------------------------


def test_not_really_text_is_refused_with_a_reason():
    result = normalize((SOURCES / "memos" / "not-really-text.txt").read_bytes(), "memos/not-really-text.txt")
    assert isinstance(result, Refusal)
    assert result.reason


def test_a_long_paragraph_with_almost_no_whitespace_is_refused():
    raw = ("x" * 5000).encode("utf-8")
    result = normalize(raw, "memos/dense.txt")
    assert isinstance(result, Refusal)


def test_ordinary_long_prose_is_not_refused():
    sentence = "The congestion working group met to review the hourly figures for the tie. "
    raw = (sentence * 80).encode("utf-8")
    result = normalize(raw, "memos/long-prose.txt")
    assert isinstance(result, Conversion)


# --- docx and pdf -------------------------------------------------------------


def test_docx_fixtures_convert_with_the_first_paragraph_as_title():
    for name in ("desk-procedures", "outage-letter"):
        conversion = _convert(f"memos/{name}.docx")
        assert conversion.paragraphs
        assert conversion.title == conversion.paragraphs[0]


def test_outage_notice_pdf_converts_to_its_text():
    conversion = _convert("memos/outage-notice.pdf")
    assert "Planned outage, Cascade tie" in conversion.paragraphs[0]


def test_signed_statement_scan_pdf_has_no_text_layer_and_a_reported_reason():
    result = normalize(
        (SOURCES / "memos" / "signed-statement-scan.pdf").read_bytes(), "memos/signed-statement-scan.pdf"
    )
    assert isinstance(result, Conversion)
    assert result.paragraphs == ()
    assert result.metadata["reason"]


# --- unclaimed suffixes and markdown headings --------------------------------


def test_an_unclaimed_suffix_is_refused():
    result = normalize(b"whatever", "sources/export.mbox")
    assert isinstance(result, Refusal)


def test_a_markdown_heading_becomes_the_title():
    result = normalize(b"# The Outage\n\nBody text here.", "notes/theme/outage.md")
    assert isinstance(result, Conversion)
    assert result.title == "The Outage"
    assert result.paragraphs == ("# The Outage", "Body text here.")


# --- byte-stable and date-free -------------------------------------------------


ALL_FIXTURES = (
    [f"mail/{p.name}" for p in sorted(MAIL.glob("*.eml"))]
    + ["memos/desk-procedures.docx", "memos/outage-letter.docx"]
    + ["memos/outage-notice.pdf", "memos/signed-statement-scan.pdf"]
    + ["memos/glossary.txt", "2001/May/desk-checklist.txt"]
)


@pytest.mark.parametrize("relative", ALL_FIXTURES)
def test_converter_output_is_byte_stable_across_two_runs(relative):
    raw = (SOURCES / relative).read_bytes()
    first = normalize(raw, relative)
    second = normalize(raw, relative)
    assert first == second


@pytest.mark.parametrize("relative", ALL_FIXTURES)
def test_no_converter_sets_or_reads_a_date(relative):
    raw = (SOURCES / relative).read_bytes()
    result = normalize(raw, relative)
    if isinstance(result, Conversion):
        assert not any("date" in key.lower() for key in result.metadata)

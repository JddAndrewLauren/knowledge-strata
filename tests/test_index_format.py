"""Header and hit-line formats match docs/drafts/skill/SKILL.md's header
block and #10's resolution, field for field, in plain text, with
``reply_tokens`` on both search and read (final acceptance checkbox).
"""

from __future__ import annotations

import pytest

from strata.embeddings import FakeEmbedder
from strata.index import LABEL_WIDTH, Index
from strata.ledger import Ledger

from factories import exact, make_source


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "ledger.db")
    yield led
    led.close()


@pytest.fixture
def index(tmp_path, ledger):
    idx = Index(tmp_path / "index.db", ledger=ledger, embedder=FakeEmbedder(), semantic=False)
    yield idx
    idx.close()


SKILL_MD_FIELDS = [
    "indexing",
    "index_revision",
    "corpus_revision",
    "lexical_total",
    "evidence_total",
    "tokens",
    "by_month",
    "undated",
    "inferred",
    "covered",
    "chunks",
    "shown",
    "continuations",
    "reply_tokens",
]


def test_header_fields_appear_in_the_documented_order_with_the_documented_column(index):
    rec = make_source(index._ledger, "a.txt", ["Content."], date=exact("2001-06-01"))
    index.sync([rec])
    text = index.search().text()
    header_lines = text.split("\n\n")[0].splitlines()
    labels_seen = [line[:LABEL_WIDTH].rstrip() for line in header_lines if not line.startswith(" ")]
    assert labels_seen == SKILL_MD_FIELDS
    for line in header_lines:
        if not line.startswith(" "):
            label = line[:LABEL_WIDTH].rstrip()
            assert line[: LABEL_WIDTH] == label.ljust(LABEL_WIDTH)


def test_list_continuation_rows_are_indented_to_the_label_column(index):
    records = []
    for month in (1, 2):
        records.append(make_source(index._ledger, f"a{month}.txt", ["A."], date=exact(f"2001-0{month}-01")))
        records.append(make_source(index._ledger, f"b{month}.txt", ["B."], date=exact(f"2001-0{month}-02")))
    index.sync(records)
    text = index.search().text()
    lines = text.split("\n\n")[0].splitlines()
    by_month_index = next(i for i, line in enumerate(lines) if line.startswith("by_month"))
    continuation_line = lines[by_month_index + 1]
    assert continuation_line.startswith(" " * LABEL_WIDTH)
    assert continuation_line[LABEL_WIDTH] != " "  # content starts right after the indent, not further in


def test_reply_tokens_appears_on_both_search_and_read(index):
    rec = make_source(index._ledger, "a.txt", ["Content."], date=exact("2001-06-01"))
    index.sync([rec])
    search_text = index.search().text()
    read_text = index.read(rec.ref).text()
    assert "reply_tokens" in search_text
    assert "reply_tokens" in read_text


def test_hit_line_field_order_is_ref_date_kind_title(index):
    rec = make_source(index._ledger, "a.txt", ["Content about the desk."], date=exact("2001-06-14"), title="A Title")
    index.sync([rec])
    hit = index.search().hits[0]
    parts = hit.line().split("  ")
    assert parts[0] == rec.ref
    assert parts[1] == "2001-06-14"
    assert parts[2] == "source"
    assert parts[3] == "A Title"

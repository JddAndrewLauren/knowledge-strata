"""strata.server: the two MCP tools over one project (issue #32 acceptance).

Every test drives the tools through an in-process MCP client
(``mcp.Client(server)``) - the seam this issue actually builds - rather
than calling :class:`strata.index.Index` directly. Hermetic: every corpus is
a throwaway copy inside ``tmp_path``, the conversion cache is a temporary
file, and the embedder is the fake one.
"""

from __future__ import annotations

import asyncio
import re
import shutil
import sys
from pathlib import Path

from strata.corpus import manuscript, notes, sources
from strata.embeddings import FakeEmbedder
from strata.index import REPLY_TOKEN_BUDGET
from strata.ledger import Ledger
from strata.server import Project, build_server, main

from mcp import Client

ROOT = Path(__file__).resolve().parent.parent
WEST_DESK = ROOT / "examples" / "west-desk"

sys.path.insert(0, str(ROOT / "scripts"))
import make_fixtures  # noqa: E402

HELD = "Tomas held the noon submission. First time I had seen him hold anything."
EDITED = "Tomas held the noon submission, reluctantly."


# -- helpers ------------------------------------------------------------------


def _write_config(folder: Path, *, corpus=(), manuscript_path=None, chunk_tokens=None) -> None:
    (folder / ".strata").mkdir(parents=True, exist_ok=True)
    lines = ["corpus:", *[f"  - {c}" for c in corpus]] if corpus else ["corpus: []"]
    if manuscript_path:
        lines.append(f"manuscript: {manuscript_path}")
    if chunk_tokens:
        lines.append(f"chunk_tokens: {chunk_tokens}")
    (folder / ".strata" / "config.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _project(tmp_path, *, corpus=(), manuscript_path=None, chunk_tokens=None, embedder=None, notes_dir=True) -> Project:
    folder = tmp_path / "project"
    _write_config(folder, corpus=corpus, manuscript_path=manuscript_path, chunk_tokens=chunk_tokens)
    if notes_dir:
        (folder / "notes").mkdir(parents=True, exist_ok=True)
    return Project(folder=folder, embedder=embedder or FakeEmbedder(), cache_db=tmp_path / "store.db")


def _call(project: Project, name: str, arguments: dict | None = None):
    async def run():
        async with Client(build_server(project)) as client:
            return await client.call_tool(name, arguments or {})

    return asyncio.run(run())


def _list_tools(project: Project):
    async def run():
        async with Client(build_server(project)) as client:
            return await client.list_tools()

    return asyncio.run(run())


def _text(result) -> str:
    assert not result.is_error, result.content
    assert len(result.content) == 1
    assert result.content[0].type == "text"
    return result.content[0].text


def _error_text(result) -> str:
    assert result.is_error
    assert len(result.content) == 1
    return result.content[0].text


def _field(text: str, label: str) -> str:
    match = re.search(rf"^{re.escape(label)}\s+(.*)$", text, re.MULTILINE)
    assert match, f"no {label!r} field in:\n{text}"
    return match.group(1)


def _int_field(text: str, label: str) -> int:
    return int(re.search(r"\d+", _field(text, label)).group())


def _hits_section(text: str) -> str:
    return text.partition("\n\n")[2]


def _first_chunk_cursor(text: str) -> str:
    row = _field(text, "chunks")
    assert row != "none", f"expected at least one chunk row in:\n{text}"
    return row.split()[-1]


# -- tool shape ----------------------------------------------------------------


def test_lists_exactly_two_read_only_tools_with_the_documented_parameter_names(tmp_path):
    project = _project(tmp_path)
    tools = {tool.name: tool for tool in _list_tools(project).tools}

    assert set(tools) == {"search", "read"}
    for tool in tools.values():
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True

    assert set(tools["search"].input_schema["properties"]) == {"query", "from", "to", "who", "kind", "cursor"}
    assert set(tools["read"].input_schema["properties"]) == {"ref", "cursor"}


def test_main_rejects_anything_but_serve_project_folder(capsys):
    assert main([]) == 2
    assert main(["init"]) == 2
    assert main(["serve"]) == 2
    assert "usage: strata serve" in capsys.readouterr().err


def test_a_str_reply_arrives_once_as_text(tmp_path):
    project = _project(tmp_path)
    result = _call(project, "search", {})
    assert not result.is_error
    assert len(result.content) == 1
    assert result.content[0].type == "text"
    assert result.structured_content is None


# -- ToolError vs sanitized crash -----------------------------------------------


def test_a_bad_ref_is_a_tool_error_naming_the_problem(tmp_path):
    project = _project(tmp_path)

    message = _error_text(_call(project, "read", {"ref": "SRC-999999"}))
    assert "SRC-999999" in message

    message = _error_text(_call(project, "read", {"ref": "nonsense"}))
    assert "nonsense" in message


def test_a_conflicting_cursor_and_argument_is_a_tool_error(tmp_path):
    project = _project(tmp_path)
    message = _error_text(_call(project, "search", {"cursor": "whatever", "query": "x"}))
    assert "cursor" in message.lower()


def test_no_config_is_a_tool_error_not_a_sanitized_crash(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir()  # no .strata/config.yaml: never initialized
    project = Project(folder=folder, embedder=FakeEmbedder())
    message = _error_text(_call(project, "search", {}))
    assert "strata init" in message


def test_an_internal_exception_is_sanitized(tmp_path, monkeypatch):
    import strata.index as index_module

    project = _project(tmp_path)

    def _boom(self, *args, **kwargs):
        raise RuntimeError("internal secret: /etc/passwd")

    monkeypatch.setattr(index_module.Index, "search", _boom)
    message = _error_text(_call(project, "search", {}))
    assert "internal secret" not in message
    assert "/etc/passwd" not in message
    assert "search" in message


def test_an_incidental_value_error_is_sanitized_too(tmp_path, monkeypatch):
    import strata.index as index_module

    project = _project(tmp_path)

    def _boom(self, *args, **kwargs):
        raise ValueError("labels leave no room: budget_bytes=-12")

    monkeypatch.setattr(index_module.Index, "read", _boom)
    message = _error_text(_call(project, "read", {"ref": "SRC-000001"}))
    assert "budget_bytes" not in message
    assert "read" in message


# -- freshness, revisions, cursor invalidation ----------------------------------


def test_note_edit_changes_index_revision_not_corpus_source_edit_changes_both_stale_cursor_rejected(tmp_path):
    project_folder = tmp_path / "project"
    source_dir = project_folder / "sources"
    source_dir.mkdir(parents=True)
    (source_dir / "a.txt").write_text(
        "16 May 2001\n\nHello there, a sentence long enough to pad the token "
        "count so a tiny chunk budget forces at least one chunk assignment.\n",
        encoding="utf-8",
    )
    (project_folder / "notes").mkdir()
    (project_folder / "notes" / "scratch.md").write_text("# Scratch\n\nOriginal text.\n", encoding="utf-8")
    _write_config(project_folder, corpus=[str(source_dir)], chunk_tokens=1)
    project = Project(folder=project_folder, embedder=FakeEmbedder(), cache_db=tmp_path / "store.db")

    text1 = _text(_call(project, "search", {}))
    index_rev_1 = _field(text1, "index_revision")
    corpus_rev_1 = _field(text1, "corpus_revision")
    stale_cursor = _first_chunk_cursor(text1)

    # -- editing a note changes index_revision, not corpus_revision ----------
    (project_folder / "notes" / "scratch.md").write_text("# Scratch\n\nEdited text.\n", encoding="utf-8")
    text2 = _text(_call(project, "search", {}))
    assert _field(text2, "index_revision") != index_rev_1
    assert _field(text2, "corpus_revision") == corpus_rev_1

    # -- the earlier cursor is rejected explicitly, naming the original scope
    message = _error_text(_call(project, "search", {"cursor": stale_cursor}))
    assert "revision changed" in message
    assert "restart the original scope" in message

    # -- editing a source changes both ----------------------------------------
    (source_dir / "a.txt").write_text(
        "16 May 2001\n\nHello there, a sentence long enough to pad the token "
        "count so a tiny chunk budget forces at least one chunk assignment, EDITED.\n",
        encoding="utf-8",
    )
    text3 = _text(_call(project, "search", {}))
    assert _field(text3, "index_revision") != _field(text2, "index_revision")
    assert _field(text3, "corpus_revision") != corpus_rev_1


# -- indexing state --------------------------------------------------------------


def test_first_index_interrupted_reports_incomplete_with_progress_and_completes_next_call(tmp_path, monkeypatch):
    import strata.server as server_module

    project_folder = tmp_path / "project"
    source_dir = project_folder / "sources"
    source_dir.mkdir(parents=True)
    (source_dir / "a.txt").write_text("16 May 2001\n\nOne dated source paragraph.\n", encoding="utf-8")
    (project_folder / "notes" / "digest").mkdir(parents=True)
    (project_folder / "notes" / "digest" / "may.md").write_text(
        "---\n"
        "window:\n  from: 2001-05-01\n  to: 2001-05-31\n"
        "corpus_revision: \"1\"\n"
        "coverage_complete: true\n"
        "---\n"
        "# Digest\n\nAll of May, read in full.\n",
        encoding="utf-8",
    )
    (project_folder / "manuscript").mkdir()
    (project_folder / "manuscript" / "ch1.md").write_text("# Chapter one\n\nSome text.\n", encoding="utf-8")
    _write_config(project_folder, corpus=[str(source_dir)], manuscript_path=str(project_folder / "manuscript"))
    project = Project(folder=project_folder, embedder=FakeEmbedder(), cache_db=tmp_path / "store.db")

    def _boom(folder):
        raise RuntimeError("injected failure mid-sync")

    monkeypatch.setattr(server_module.manuscript, "read", _boom)

    text = _text(_call(project, "search", {"from": "2001-05-01", "to": "2001-05-31"}))
    assert _field(text, "indexing").startswith("incomplete")
    assert "2" in _field(text, "indexing")  # progress: the source and the digest note
    assert _field(text, "covered") == "none"  # design.md: no coverage while a first index is incomplete

    monkeypatch.undo()
    text2 = _text(_call(project, "search", {"from": "2001-05-01", "to": "2001-05-31"}))
    assert _field(text2, "indexing") == "complete"
    assert "notes/digest/may.md" in _field(text2, "covered")


# -- hermetic demo e2e over the west-desk fixture --------------------------------


def test_demo_e2e_over_west_desk(tmp_path):
    project_folder = tmp_path / "project"
    sources_dir = project_folder / "sources"
    shutil.copytree(WEST_DESK / "sources", sources_dir)
    manuscript_dir = project_folder / "manuscript"
    shutil.copytree(WEST_DESK / "manuscript", manuscript_dir)
    shutil.copytree(WEST_DESK / "notes", project_folder / "notes")
    busy_dir = project_folder / "busy"
    make_fixtures.scale_busy_day(busy_dir, day="Mon, 15 Oct 2001")

    _write_config(
        project_folder,
        corpus=[str(sources_dir), str(busy_dir)],
        manuscript_path=str(manuscript_dir),
        chunk_tokens=500,
    )
    project = Project(folder=project_folder, embedder=FakeEmbedder(), cache_db=tmp_path / "store.db")
    replies: list[str] = []

    def reply(result) -> str:
        """Every reply this test receives, so the cap below covers all of
        them - the browse continuation pages included."""
        replies.append(_text(result))
        return replies[-1]

    # -- ground truth record counts, from the adapters directly ---------------
    truth_ledger = Ledger(tmp_path / "truth-ledger.db")
    try:
        truth_sources = len(
            sources.sync([sources_dir, busy_dir], truth_ledger, cache_db=tmp_path / "truth-store.db").records
        )
    finally:
        truth_ledger.close()
    truth_notes = len(notes.read(project_folder / "notes"))
    truth_manuscript = len(manuscript.read(manuscript_dir).records)

    # -- record counts by kind -------------------------------------------------
    assert _int_field(reply(_call(project, "search", {"kind": "source"})), "evidence_total") == truth_sources
    assert _int_field(reply(_call(project, "search", {"kind": "note"})), "evidence_total") == truth_notes
    assert (
        _int_field(reply(_call(project, "search", {"kind": "manuscript"})), "evidence_total") == truth_manuscript
    )

    # -- a manuscript heading ref resolves --------------------------------------
    body = reply(_call(project, "read", {"ref": "manuscript/ch02-the-cutoff.md # Morning (2)"}))
    assert "The second heading called Morning is here on purpose" in body

    # -- who: PV finds Priya's mail ----------------------------------------------
    who_text = reply(_call(project, "search", {"who": "PV", "kind": "source"}))
    assert _int_field(who_text, "evidence_total") >= 1

    # -- a retired anchor (editing a source between calls) reads back with -----
    # -- a marker and its exact original text -----------------------------------
    query_text = reply(_call(project, "search", {"query": "noon submission"}))
    first_hit_line = _hits_section(query_text).splitlines()[0]
    ref = first_hit_line.split("  ")[0]

    journal = sources_dir / "2001" / "June" / "journal-outage-week.txt"
    journal.write_text(journal.read_text(encoding="utf-8").replace(HELD, EDITED), encoding="utf-8")

    retired_text = reply(_call(project, "read", {"ref": ref}))
    assert any("retired" in line for line in retired_text.splitlines())
    assert HELD in retired_text

    # -- the eligible May digest appears in covered; the stale, legacy and -----
    # -- split ones do not --------------------------------------------------------
    real_revision = _field(reply(_call(project, "search", {"from": "2001-05-01", "to": "2001-05-31"})), "corpus_revision")
    digest_dir = project_folder / "notes" / "digest"
    may = digest_dir / "2001-05-01--2001-05-31.md"
    # Quoted: the placeholder is a bare (unquoted) YAML string, but a real
    # revision is all-digits and would otherwise parse as an int, which the
    # notes adapter rejects as not-a-string and clears (record.py).
    may.write_text(
        may.read_text(encoding="utf-8").replace("SET-BY-TEST-AFTER-SYNC", f'"{real_revision}"'), encoding="utf-8"
    )

    covered_text = reply(_call(project, "search", {"from": "2001-04-01", "to": "2001-07-31"}))
    covered = _field(covered_text, "covered")
    assert "notes/digest/2001-05-01--2001-05-31.md" in covered
    assert "notes/digest/2001-05-01--2001-05-31-2.md" not in covered  # stale
    assert "notes/digest/2001-06-01--2001-06-30.md" not in covered  # legacy
    assert "notes/digest/2001-07-01--2001-07-16.md" not in covered  # split

    # -- browse over the busy day enumerates more than 500 records through -----
    # -- continuations with no duplicates ------------------------------------------
    seen: list[str] = []
    browse_pages = 0
    args = {"kind": "source", "from": "2001-10-15", "to": "2001-10-15"}
    for _ in range(20):
        text = reply(_call(project, "search", args))
        browse_pages += 1
        seen.extend(line.split("  ")[0] for line in _hits_section(text).splitlines() if line.strip())
        cursor = _field(text, "continuations")
        if cursor == "none":
            break
        args = {"cursor": cursor}
    else:
        raise AssertionError("did not reach the end of the busy day's continuations")
    assert len(seen) > 500
    assert len(seen) == len(set(seen))

    # -- every reply stays within the ~8,000-token estimate and ends with -------
    # -- reply_tokens -----------------------------------------------------------------
    assert browse_pages >= 2  # the continuation pages are the replies most likely to overrun
    for text in replies:
        # A search reply carries reply_tokens as the header's closing field,
        # hits after it; a read reply ends with it. Either way it is the
        # reply's own estimate of itself, and it must fit the budget.
        assert re.search(r"^reply_tokens\s+~\d+$", text, re.MULTILINE), f"no reply_tokens line in:\n{text}"
        assert _int_field(text, "reply_tokens") <= REPLY_TOKEN_BUDGET

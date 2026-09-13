"""strata.server: the two MCP tools over one project's index (design.md
"The shape", "Two tools"). Entry point for ``strata serve <project-folder>``.

One local, stdio MCP server over one project folder, exposing exactly
``search`` and ``read`` and nothing else - the rule behind memoria's
``read`` and ``search_text`` tools (``mcp/server.py``, about 350 of its
1,660 lines): search never returns whole documents, read never summarizes.
No write tools, ever.

**Fresh as of this call** (design.md "Freshness"): before answering, every
call walks the corpus roots and the ``notes``/manuscript folders through the
three adapters and hands the result to :meth:`~strata.index.Index.sync`, so
the reply reflects the project folder as it is right now. Whether that runs
per call or behind a file watcher is an implementation choice behind the
seam (design.md); per call is where this issue starts, and it is the first
real-corpus failure to watch for - a per-call stat-walk over a large real
archive is too slow to feel invisible.

Sync ``def`` tools run on a worker thread the SDK picks per call
(docs/research/python-stack.md), not necessarily the same thread twice, so
nothing here keeps a ``sqlite3`` connection open across calls: ``Ledger``
and ``Index`` are opened fresh inside every call and closed before it
returns. Only the embedder - not a sqlite object - is built once, outside
any call, and reused.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Annotated, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from strata import config, refs
from strata.corpus import manuscript, notes, sources
from strata.embeddings import Embedder, FastEmbedEmbedder
from strata.index import BadRef, CursorError, Index, SearchReply, estimate_tokens
from strata.ledger import Ledger

_READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
# The model-visible errors (design.md "Two tools"): a bad ref, a conflicting or
# invalidated cursor, a project with no config. Every other exception - an
# incidental ValueError included - is left for the SDK to sanitize.
_MODEL_VISIBLE = (BadRef, refs.BadRef, CursorError, config.ConfigError)
_T = TypeVar("_T")


@dataclass(frozen=True)
class Project:
    """One project folder's fixed identity: paths plus the one long-lived,
    thread-safe collaborator (the embedder). ``Ledger`` and ``Index`` hold a
    ``sqlite3.Connection`` each, so they are opened fresh inside every call
    instead of living here. ``cache_db`` overrides the conversion cache
    (``~/.strata/cache/store.db`` when ``None``); tests point it at a
    temporary file so the hermetic suite never touches a real home directory.
    """

    folder: Path
    embedder: Embedder
    cache_db: Path | None = None

    @property
    def ledger_path(self) -> Path:
        return self.folder / ".strata" / "ledger.db"

    @property
    def index_path(self) -> Path:
        return self.folder / ".strata" / "cache" / "index.db"


def _resolve(folder: Path, maybe_relative: str) -> Path:
    path = Path(maybe_relative)
    return path if path.is_absolute() else folder / path


def _sync_now(project: Project, cfg: config.ProjectConfig, ledger: Ledger, index: Index) -> None:
    """Walk the corpus roots and the ``notes``/manuscript folders and hand
    every Record to ``index.sync`` - "fresh as of this call". A failure
    partway hands whatever Records were already collected to
    ``index.sync(..., complete=False)`` - real progress, not just a flag -
    unless the index was already complete: one bad call must not retract a
    project whose first index has already finished (design.md "Indexing
    state" is about a *first* index; a transient failure on a later refresh
    just serves the last-known-good, still-complete state)."""
    records = []
    try:
        roots = [_resolve(project.folder, root) for root in cfg.corpus]
        records.extend(sources.sync(roots, ledger, cache_db=project.cache_db).records)
        notes_folder = project.folder / "notes"
        if notes_folder.is_dir():
            records.extend(notes.read(notes_folder))
        if cfg.manuscript:
            records.extend(manuscript.read(_resolve(project.folder, cfg.manuscript)).records)
    except Exception as error:
        print(f"strata: sync failed after {len(records)} records: {error!r}", file=sys.stderr)
        if index.indexing_state != "complete":
            index.sync(records, complete=False)
        return
    index.sync(records, complete=True)


def _call(project: Project, fn: Callable[[Index], _T]) -> _T:
    """Open ``Ledger``/``Index`` fresh, sync now, run ``fn(index)``, close -
    every tool call goes through this so freshness and connect-per-call both
    hold for every reply. ``config.load`` runs first and is left to raise:
    with no config there is nothing to sync."""
    cfg = config.load(project.folder)
    ledger = Ledger(project.ledger_path)
    project.index_path.parent.mkdir(parents=True, exist_ok=True)
    index = Index(project.index_path, ledger=ledger, embedder=project.embedder, chunk_tokens=cfg.chunk_tokens)
    try:
        _sync_now(project, cfg, ledger, index)
        return fn(index)
    finally:
        index.close()
        ledger.close()


def _suppress_coverage_if_incomplete(reply: SearchReply) -> SearchReply:
    """design.md "Indexing state": while a first index is incomplete, the
    server grants no coverage and implies no completeness, whatever
    ``covered`` rows a partial index happens to carry."""
    if reply.indexing == "complete" or not reply.covered:
        return reply
    draft = replace(reply, covered=[], reply_tokens=0)
    return replace(draft, reply_tokens=estimate_tokens(draft.text()))


def build_server(project: Project) -> MCPServer:
    """One :class:`MCPServer` over ``project``, exposing exactly the two
    read-only tools (design.md "Two tools")."""
    mcp = MCPServer(
        "knowledge-strata",
        instructions="Persistent, accessible memory across a large corpus. search() finds evidence; "
        "read() returns its exact text. Neither tool ever writes.",
    )

    @mcp.tool(title="Search the archive", annotations=_READ_ONLY, structured_output=False)
    def search(
        query: str = "",
        from_: Annotated[str | None, Field(validation_alias="from")] = None,
        to: str | None = None,
        who: str | None = None,
        kind: str | None = None,
        cursor: str | None = None,
    ) -> str:
        """Hybrid lexical/semantic search: a header (coverage, counts, an
        approximate token cost, executable reader assignments) then hits.
        Every filter is optional; an empty query with a date range is a
        timeline browse. ``cursor`` alone resumes a reply's unfinished
        lists at their original scope; no other argument may accompany it."""
        try:
            reply = _call(
                project,
                lambda index: index.search(query=query, from_=from_, to=to, who=who, kind=kind, cursor=cursor),
            )
        except _MODEL_VISIBLE as error:
            raise ToolError(str(error)) from error
        return _suppress_coverage_if_incomplete(reply).text()

    @mcp.tool(title="Read verbatim text", annotations=_READ_ONLY, structured_output=False)
    def read(ref: str = "", cursor: str | None = None) -> str:
        """Verbatim text for a source paragraph/range, a whole source
        record, a note path or a manuscript heading section - never a
        summary. ``cursor`` alone resumes a prior read's exact position;
        ``ref`` may be omitted with it, or must match."""
        try:
            reply = _call(project, lambda index: index.read(ref=ref, cursor=cursor))
        except _MODEL_VISIBLE as error:
            raise ToolError(str(error)) from error
        return reply.text()

    return mcp


def main(argv: list[str] | None = None) -> int:
    """``strata serve <project-folder>``: the command ``.mcp.json``
    registers (issue #33 writes that file). Stdio transport."""
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] != "serve":
        print("usage: strata serve <project-folder>", file=sys.stderr)
        return 2
    project = Project(folder=Path(argv[1]).resolve(), embedder=FastEmbedEmbedder())
    build_server(project).run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

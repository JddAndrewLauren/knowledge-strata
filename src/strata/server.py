"""strata.server: the two MCP tools over one project's index (design.md
"The shape", "Two tools"). Entry point for ``strata serve <project-folder>``.

One local, stdio MCP server over one project folder, exposing exactly
``search`` and ``read`` and nothing else - the rule behind memoria's
``read`` and ``search_text`` tools (``mcp/server.py``, about 350 of its
1,660 lines): search never returns whole documents, read never summarizes.
No write tools, ever.

**Fresh as of this call:** every tool runs the shared ``Project.current``
refresh while holding the project lock. Failed refreshes return an explicit
incomplete tool error, never stale results presented as current. SQLite
connections are opened and closed within the tool's worker thread.

"""

from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Annotated, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from strata import config, refs
from strata.index import BadRef, CursorError, Index, SearchReply, estimate_tokens
from strata.project import Project, RefreshFailed

_READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
# The model-visible errors (design.md "Two tools"): a bad ref, a conflicting or
# invalidated cursor, a project with no config. Every other exception - an
# incidental ValueError included - is left for the SDK to sanitize.
_MODEL_VISIBLE = (BadRef, refs.BadRef, CursorError, config.ConfigError, RefreshFailed)
_T = TypeVar("_T")


def _call(project: Project, fn: Callable[[Index], _T]) -> _T:
    """Serialize refresh and query; failed refreshes serve no current results."""
    with project.current() as index:
        return fn(index)


def _suppress_coverage_if_incomplete(reply: SearchReply) -> SearchReply:
    """design.md "The user's experience": while a first index is incomplete, the
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
        return "indexing: complete\n" + reply.text()

    return mcp


def main(argv: list[str] | None = None) -> int:
    """``strata serve <project-folder>``: the command ``.mcp.json``
    registers (issue #33 writes that file). Stdio transport."""
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2 or argv[0] != "serve":
        print("usage: strata serve <project-folder>", file=sys.stderr)
        return 2
    project = Project(folder=Path(argv[1]).resolve())
    build_server(project).run(transport="stdio")
    return 0


# Programmatic runtime entry points used by integration/stdio journeys.
create_server = build_server


def run(project: Project):
    build_server(project).run(transport="stdio")


if __name__ == "__main__":
    raise SystemExit(main())

"""Enron tier (marker ``e2e``): the same assertions, at scale, through the
MCP server boundary this issue builds. Deselected by default
(``-m "not e2e"`` in ``addopts``); runs only on the author's machine against
the real archive with the real embedder and fails, never skips, without
``STRATA_ENRON_ROOT`` (wayfinder #12, CONTEXT.md "Enron tier").

Demonstrates, not verifies (CONTEXT.md): a busy month carries ``chunks``, a
quiet week none, and no reply exceeds the ~8,000-token estimate. It cannot
establish support for the actual user's formats - that is the acceptance
gate (``docs/acceptance.md``).
"""

from __future__ import annotations

import asyncio
import os
import re

import pytest
from mcp import Client

from strata.embeddings import FastEmbedEmbedder
from strata.index import REPLY_TOKEN_BUDGET
from strata.server import Project, build_server

pytestmark = pytest.mark.e2e

# The Enron collapse became public news in late 2001, so message volume
# through the desks named in the FERC production spikes hard in this month;
# by early 2003 most mailboxes in the production are quiet. Override either
# with the matching env var if a different pair demonstrates the property
# better against the operator's own copy of the archive.
_DEFAULT_BUSY_MONTH = ("2001-12-01", "2001-12-31")
_DEFAULT_QUIET_WEEK = ("2003-01-06", "2003-01-12")


def _call(project: Project, name: str, arguments: dict) -> str:
    async def run():
        async with Client(build_server(project)) as client:
            return await client.call_tool(name, arguments)

    result = asyncio.run(run())
    assert not result.is_error, result.content
    return result.content[0].text


def _field(text: str, label: str) -> str:
    match = re.search(rf"^{re.escape(label)}\s+(.*)$", text, re.MULTILINE)
    assert match, f"no {label!r} field in:\n{text}"
    return match.group(1)


def test_enron_tier_busy_month_carries_chunks_a_quiet_week_none(tmp_path):
    root = os.environ.get("STRATA_ENRON_ROOT")
    if not root:
        pytest.fail(
            "STRATA_ENRON_ROOT is not set; the Enron tier fails rather than skips "
            "(wayfinder #12, CONTEXT.md 'Enron tier')"
        )

    project_folder = tmp_path / "project"
    (project_folder / ".strata").mkdir(parents=True)
    (project_folder / ".strata" / "config.yaml").write_text(f"corpus:\n  - {root}\n", encoding="utf-8")
    (project_folder / "notes").mkdir()
    project = Project(folder=project_folder, embedder=FastEmbedEmbedder())

    busy_from = os.environ.get("STRATA_ENRON_BUSY_FROM", _DEFAULT_BUSY_MONTH[0])
    busy_to = os.environ.get("STRATA_ENRON_BUSY_TO", _DEFAULT_BUSY_MONTH[1])
    quiet_from = os.environ.get("STRATA_ENRON_QUIET_FROM", _DEFAULT_QUIET_WEEK[0])
    quiet_to = os.environ.get("STRATA_ENRON_QUIET_TO", _DEFAULT_QUIET_WEEK[1])

    busy_text = _call(project, "search", {"from": busy_from, "to": busy_to, "kind": "source"})
    quiet_text = _call(project, "search", {"from": quiet_from, "to": quiet_to, "kind": "source"})

    assert _field(busy_text, "chunks") != "none", f"expected a busy month ({busy_from}..{busy_to}) to carry chunks"
    assert _field(quiet_text, "chunks") == "none", f"expected a quiet week ({quiet_from}..{quiet_to}) to carry none"

    for text in (busy_text, quiet_text):
        tokens = int(re.search(r"~(\d+)", _field(text, "reply_tokens")).group(1))
        assert tokens <= REPLY_TOKEN_BUDGET

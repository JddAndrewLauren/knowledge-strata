# Knowledge Strata

Persistent, cited memory for research and writing over a local archive. A project
holds searchable sources, Markdown notes, and an optional manuscript. Claude Code
uses two MCP tools (`search` and `read`); source paragraph citations keep their exact
stored text when originals change.

This is an early implementation. Automated retrieval, recovery, and MCP journeys
are tested. A small real-host recovery journey passed; complete host and clean Windows/macOS acceptance remain open. The current
refresh strategy is expensive on large archives; see [measured limits](docs/evidence/README.md).

## Install and start

Prerequisites: Git, Python 3.12 or later, and Claude Code authenticated on the same
machine. First use of semantic search downloads a local embedding model; archive
conversion and embedding run locally. Source excerpts sent to Claude follow the
host's account and data policies.

For development, in this checkout:

```sh
python -m pip install -e ".[dev]"
```

For distribution without repository access, provide the built wheel to the user:

```sh
uv tool install --python 3.12 ./strata-0.0.1-py3-none-any.whl
```

There is no published release from this change. Build a wheel with
`python -m build --wheel` (install `build` first).

Create a separate project folder, then run:

```sh
strata init --corpus /path/to/archive --manuscript /path/to/manuscript
claude
```

Omit `--manuscript` if there is none. Repeat `--corpus` for additional folders.
Quote paths with spaces, including Windows paths. A project may contain a corpus
subfolder, but cannot itself be inside a corpus root. Supported source formats
are text, Markdown, EML, DOCX, and text-layer PDF; manuscripts are Markdown.
Scans, attachments, and mail container formats are not converted.

Ask a question about the archive. Ask for the exact passage to inspect its wording.
A source citation such as `SRC-000001 p2` identifies frozen converted text, not raw
file bytes. Deleted or edited paragraphs remain readable with retirement markers.

Setup creates `.strata/config.yaml`, the durable ledger, `notes/project.md`, MCP
registration, and a project permission allowlist. It installs the packaged skill
and reader under `~/.claude/`. Existing notes, manuscript, unrelated settings,
other MCP servers, and permission denies are preserved. Git is initialized when
needed. Setup commits its changed setup files when the staging area is empty;
existing staged changes defer that commit. A local fallback Git identity is set
when no identity is configured. The skill commits its task's changes.
When rerunning init with path flags, supply every path to retain: omitted
manuscript settings are removed.

## Refresh, update, and recover

`strata index` refreshes the current project. Every tool call also performs a
serialized refresh. A concurrent refresh is allowed two seconds to release its
lock before the tool returns an incomplete error asking for a retry. Progress goes to stderr; a failed refresh returns an explicit
incomplete error instead of presenting an old snapshot as current. Restore an
unavailable archive and retry. Interrupted scans retire nothing; interrupted
index writes roll back. If the ledger advanced before an index failure, the next
refresh reconciles it before any result is served.

A bare `strata init` refreshes the project and reinstalls the packaged skill and
reader. With flags it replaces the corresponding configured paths. For an update,
install the replacement wheel with `uv tool install --force --python 3.12 PATH`,
then run `strata init` in each project to refresh its interpreter registration and
workflow files. Restart the host session after updating.

For a ledger created before persistent root identities, the original root order
must be supplied explicitly once:

```sh
strata index --legacy-root /original/first/root --legacy-root /original/second/root
```

Use the original deduplicated order, not a newly reordered configuration.
Migration rejects mappings whose live paths resolve only under another root.
Identical relative paths across roots remain ambiguous: verify the original order.
Existing duplicate identities from legacy nested roots are retained; new overlaps
do not create duplicates. Migration retains IDs, versions, and anchor text. If that order is unknown, preserve the ledger
and recover the mapping from old configuration/history; do not guess or delete it.

Back up `.strata/ledger.db`, `.strata/config.yaml`, notes, and manuscript with the
server stopped. Never delete the ledger. `.strata/cache/` and `~/.strata/cache/`
are disposable; removing them with the server stopped costs rebuilding time.
The user cache shares conversions and model-specific paragraph embeddings across
projects. Set `STRATA_CACHE_DIR` to relocate that disposable cache. An interrupted download can be retried with `strata index`.

A note edit invalidates pending cursors but does not invalidate source coverage.
A source change invalidates coverage conservatively. Restart the original scope;
never concatenate partial reads from different revisions. Metadata-only read pages
are labeled separately and may precede source text. Cursors are navigation, never
citations. Coverage frontmatter is an agent assertion checked for eligibility;
it is not independent proof that the model read or retained every detail.

## Verification

```sh
pytest -q
python scripts/evaluate.py --real-model
python scripts/benchmark.py --records 1000
python scripts/benchmark.py --records 10000
python scripts/benchmark.py --records 100000
```

Run benchmark sizes in separate processes. Fake embeddings are the benchmark
default; add `--real-model` for real embedding costs. The ordinary test suite
uses invented inputs and no model download. See [evaluation procedures](docs/evaluation.md)
and the still-open [acceptance gate](docs/acceptance.md).

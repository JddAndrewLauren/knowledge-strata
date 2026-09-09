# Knowledge Strata - domain language

The terms this project has settled on, in the order a reader meets them. Added to
as wayfinder tickets resolve; the spec is `docs/design.md`, decisions are in
`docs/adr/`.

## Corpus and units

**Corpus** - a folder tree of raw files, external to the project, listed in
`.strata/config.yaml`. Shared between projects by listing the same root twice.

**Raw unit** - the thing that gets an id. Today a raw file; a message inside an
email export is the same idea at finer grain. Numbered on first sight, and that
number is never reused.

**Kind** - `source`, `note` or `manuscript`. Decides the ref shape and which
Record fields apply.

**Version** - a conversion of a raw unit. A changed file or a bumped converter
produces version n+1; refs do not carry a version, and resolve against the newest.

## Refs

**Ref** - the string that addresses something readable. Two shapes, decided by
kind.

**Durable ref** - resolves to the same text indefinitely, or says it was retired.
Source refs and bare file paths. A vocabulary term, not a predicate in code.

**Positional ref** - points inside a live artifact and can rot or silently
retarget. A manuscript ref carrying a heading. Never stored in a note as if
durable.

**Anchor** - a paragraph's durable number within a record, written `p17`. An id,
not a position: `p17` may be the 12th paragraph in document order.

**Retired anchor** - a number whose paragraph vanished at some version. Never
reused, and `read` says so rather than resolving it to a neighbour.

**Match key** - the normalized form of a paragraph used only for aligning a
reconversion to existing anchors: NFKC, casefolded, quotes and dashes
straightened, whitespace collapsed, trimmed. Never what gets stored.

**Continuation ref** - the open-ended tail form, `SRC-000184 p31-`, that a capped
`read` hands back so the agent never computes an end number.

## Records

**Record** - the only thing the index accepts. One frozen dataclass; per-kind
fields are rejected at construction rather than documented.

**Date** - a value object: ISO-sortable date, confidence (`exact` / `inferred` /
`unknown`), granularity (`day` / `month` / `year`), and the source's own wording.
Empty date exactly when confidence is `unknown` - there is no invented date. One
date, not memoria's recorded/event pair. A hint for search, never a gate: a date
filter drops only what is known to fall outside the range.

**Partial date** - a date known only to a month or a year (a `2013/November/`
folder, an all-numeric `11/4/2013` whose day and month cannot be told apart).
Stored as the first of its period with confidence `inferred` and its granularity
set, so it sorts and filters by period overlap. A hit shows it at its own
granularity, `2013-11 (folder)`, and never the stored first day.

**Dating strategy** - one internal rung of the dating module, tried in order,
first hit wins: email headers, the unit's first line or heading, path patterns,
docx/pdf metadata. Sources run the whole chain, manuscript chapters only the
first-line and path rungs, notes none.

**Title** (also **gist**) - the extractive one-liner every hit carries, so a scan
of many hits is cheap. Adapter-supplied, never empty, never model-written.

**Note type** - the folder a note sits in under `notes/`, singular
(`person`, `place`, `deal`). An open vocabulary; `person`, `event` and `theme`
are the starting set. Two names are reserved because code depends on them:
`project.md` and `digest/`.

**Project note** - `notes/project.md`, the one note the skill reads first.
Each chapter write appends a paragraph to it. Created by init, never renamed.

**Digest** - a note under `notes/digest/`, named by its window, written by a
reader for one fan-out chunk. The only note type whose window the header
treats as coverage.

**Window** - a note's declared coverage, `from` and `to`. Optional on any note;
the search header's coverage check reads it from digests only.

**Alias** - another name form a note answers to (first name, email address,
initials). Seeded from the forms seen when the note is created. The `who`
filter expands through the aliases of every note, whatever its type.

## Storage

**Ledger** (`.strata/ledger.db`) - durable, never dropped. Ids, versions and
anchor identity. The only irreplaceable per-project state besides `notes/`.

**Index** (`.strata/cache/index.db`) - derived and disposable. Rebuild is
`rm -rf .strata/cache/`, then sync replays the ledger.

**Header** - what search returns before hits: totals, estimated tokens, counts by
month, undated and inferred counts, digests already covering the window,
suggested chunks, how many hits are shown, and the reply's own size. What makes
fan-out decisions cheap and deterministic; the skill reads it and never counts
or partitions in prose. Totals count what matched the words and filters, never
what is semantically near.

**Hit** - one matched record, shown as ref, date, kind and title, with a
snippet when there was a query. One hit per record, never per paragraph; its
ref is the anchor of the best-matching paragraph.

**Chunk** - a contiguous run of days the header suggests one reader take,
sized to the chunk budget. Days a digest already covers are left out. A day
too big for one chunk is split, and those chunks name their first and last
ref.

**Chunk budget** - the server-side size of one chunk, in estimated tokens,
sized for the reader. Independent of the in-session budget, which the skill
owns and which sizes the session's own model.

**Coverage** - a digest's window overlapping the searched range. Reported so
the skill can skip what is already digested; applied only to chunk
suggestion, never subtracted from the totals.

**Reply size** - the estimated tokens of a search or read reply, stated at
its end so the skill can tell when a session should compact.

**Drift report** - what a reconversion tells the user changed. Contents not yet
decided.

## Stand-ins

**Stand-in** - invented material that takes the place of a side of the system the
real corpus lacks (a manuscript, a note set, a docx with a template date) so the
decision about that side can be made and tested rather than deferred. Never
copied from the corpus.

**Demo project** - the one committed stand-in, `examples/west-desk/`: a short
memoir over the corpus with its notes and a machine-local config template. Both
the thing shown working end to end and the fixture source for the manuscript and
notes adapters.

## Project folder

**Project** - a folder holding `.strata/`, `notes/`, `.mcp.json` and a
`.claude/settings.json` allowlist. Also a git repository the user never
manages: init creates it, the skill commits at the end of each task, and there
is no remote.

**Refresh** - a bare `strata init` in an initialized folder: re-sync and
rewrite the user-level skill and reader agent. How a new version reaches them.

**Cache** - anywhere named `cache/`, at project or user level. Disposable by
definition; deleting it costs time, never citations.

## Host

**Host** - what runs the agent: Claude Code, as the CLI or the Claude Desktop
app's Code tab (one runtime). Not Desktop chat and not Cowork, which lack a
working directory, a write path and model-selectable sub-agents. Decided
2026-09-08; the facts are in `docs/research/claude-desktop-hosting.md`.

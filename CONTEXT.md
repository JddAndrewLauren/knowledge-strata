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
Source paragraph anchors carry the exact-text guarantee. Bare record refs and
file paths name live artifacts and do not freeze their wording. A vocabulary
term, not a predicate in code.

**Positional ref** - points inside a live artifact and can rot or silently
retarget. A manuscript ref carrying a heading. Never stored in a note as if
durable.

**Anchor** - a paragraph's durable number within a record, written `p17`. An id,
not a position: `p17` may be the 12th paragraph in document order.

**Retired anchor** - a number whose paragraph changed or vanished at some version. Never
reused, and `read` says so rather than resolving it to a neighbour: a marker line
naming the version it retired at, the verbatim text it cited, and a pointer to
the bare record ref. Never a suggested replacement.

**Retired text** - the verbatim paragraph behind a retired anchor, copied into the
ledger at the moment it changes or vanishes and never pruned. Current exact
paragraph text is also durable, so retirement remains possible
after cache deletion and raw-file changes or removal.

**Drift** - what a reconversion changed: anchors kept, retired and added. Ledger
state, not a report. Surfaced lazily by `read` on a retired anchor; the CLI
prints one line per changed record. Reconversion never asks for confirmation.

**Match key** - exact stored paragraph text, without normalization. Hashes may
accelerate lookup but equality decides. Cosmetic edits retire anchors too;
identical duplicates pair in document order.

**Continuation cursor** - an opaque temporary navigation handle for `search`
or `read`, retaining selection, filters, ordering/offset and index revision.
An assignment cursor executes a bounded reader slice. Cursors are not citations;
revision changes invalidate them explicitly. Restart and deduplicate records,
rereading changed ones rather than mixing text pages from different revisions.

**Range** - inclusive traversal between live anchors in document order, never
numeric anchor order. Retired/missing endpoints yield a diagnostic; a reversed
range errors. Single retired anchors remain readable.

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
A current overview of at most 2,000 words, updated after chapter writes. Older
entries move to searchable history notes with links and citations preserved.
Created by init, never renamed; loaded through bounded `read` pages.

**Digest** - a note under `notes/digest/`, named by its window, written by a
reader for an assignment, or saved by the caller as a completed window aggregate.
Only a complete source-wide window digest at the current corpus revision can
earn coverage credit; a split assignment's digest alone cannot.

**Window** - a note's declared coverage, `from` and `to`. Optional on any note;
the search header's coverage check reads it from digests only.

**Alias** - another name form a note answers to (first name, email address,
initials). Seeded from the forms seen when the note is created. The `who`
filter expands through the aliases of every note, whatever its type.

## Storage

**Ledger** (`.strata/ledger.db`) - durable, never dropped. Ids, versions,
anchor identity, exact current and retired text, and corpus revision. The only
irreplaceable per-project state besides `notes/`.

**Index** (`.strata/cache/index.db`) - derived and disposable. Rebuild is
`rm -rf .strata/cache/`, then sync replays the ledger.

**Header** - bounded search metadata: lexical and evidence counts, estimated
full-record reading cost, month counts, unknown/inferred counts, eligible digest
coverage, executable assignments, shown hits and reply size. Lists paginate
within the same approximately 8,000-token whole-reply limit as hits.

**Evidence set** - for queries, all lexical matching paragraphs plus the top 200
semantic paragraphs under the same filters, deduplicated into records. Counts
labeled lexical remain lexical; reading estimates and assignments use the union.
Semantic retrieval is relevance-limited; browse enumerates the whole archive.

**Hit** - one evidence record: ref, date, kind, title and query snippet. Source
hits cite their best-matching paragraph. 100 query / 500 browse hits are maximum
page sizes, never total limits.

**Chunk** - a server-issued executable reader assignment over evidence records
(or read segments of an oversized record), with cursor, scope and estimated cost.
Assignments partition the plan without duplicating undated/partial-date records;
only eligible source coverage suppresses assignments. No bounding-ref guesswork.

**Chunk budget** - estimated read tokens per assignment, server default 80,000,
optionally configured by `chunk_tokens`; independent of session capacity.

**Index revision** - snapshot identity carried by cursors; changes explicitly
invalidate navigation, including note edits and cache rebuilds.

**Corpus revision** - durable server-issued source-state identity; advances for
source content, membership, conversion or dating changes, not note/manuscript
edits or unchanged cache-only rebuilds. Conservative corpus-wide invalidation.

**Coverage** - credit only for `notes/digest/` with valid `window`,
`coverage_complete: true` and current `corpus_revision`, after complete
source-wide reading (no query/who, kind source, all pages and unknown/overlapping
dates included). Legacy, stale, filtered and incomplete digests remain searchable
but suppress nothing. Applied only to assignments, never evidence totals or hits;
overlap alone does not prove inclusion of unknown or coarse-dated records.

**Reduction** - a saved cited summary of at most four saved digests/summaries,
at most 1,200 words. Recurses in groups of at most four, retaining child links,
source citations, omissions, contradictions and incomplete status at every level.
Reduction alone cannot confer coverage.

**Recovery note** - durable task progress, saved after every reader batch before
another starts and before context pressure. Includes completed/pending assignments,
filters, revisions, saved digest/reduction links and gaps. Fresh sessions start
from the bounded project overview and follow these links.
Growing completion records live in bounded linked recovery notes, separating
whole-record completion from server-issued read segments. A reader returns before
its progress record exceeds the digest cap. After batch note writes invalidate
cursors, a fresh plan reuses verified source completion only at the unchanged
corpus revision; changed notes/manuscript require fresh reads.

**Reply size** - the estimated tokens of a search or read reply, stated at
its end so the skill can tell when a session should compact.

**Drift report** - no separate artifact. Read shows retirement and exact text;
the CLI prints one line per changed record.

## Stand-ins

**Stand-in** - invented material that takes the place of a side of the system the
real corpus lacks (a manuscript, a note set, a docx with a template date) so the
decision about that side can be made and tested rather than deferred. Never
copied from the corpus.

**Demo project** - the one committed stand-in, `examples/west-desk/`: a short
memoir over the corpus with its notes and a machine-local config template. Both
the thing shown working end to end and the fixture source for the manuscript and
notes adapters.

**Fixture** - invented material a test runs against. Structurally faithful to
the real corpus (the stray header line, CRLF, the ZL footer, a template date in
docx metadata) and textually made up, with RFC 2606 addresses and people who do
not exist. Never a copy of a real file, scrubbed or otherwise. The demo project's
`sources/` folder is the fixture corpus for the sources adapter.

**Hermetic** - a test that needs no network, no model download and no path
outside the clone. Every test in the ordinary suite is hermetic, and the suite
shows zero skips: a skipped test is a failed one.

**Enron tier** - the one end-to-end test that is not hermetic, marked `e2e` and
deselected by default. Runs on the author's machine against the real corpus
with the real embedder, and demonstrates real-model scale. It cannot establish support for the actual
user's formats or the full user journey; those require the acceptance gate. Build issues call
what it covers *demonstrated*; what the hermetic tiers cover is *verified*.

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

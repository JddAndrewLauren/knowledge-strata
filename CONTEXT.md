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
Source refs and bare file paths. `refs.is_durable` answers this.

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
`unknown`), and the source's own wording. Empty date exactly when confidence is
`unknown` - there is no invented date. One date, not memoria's recorded/event
pair.

**Title** (also **gist**) - the extractive one-liner every hit carries, so a scan
of many hits is cheap. Adapter-supplied, never empty, never model-written.

**Note type** - `person`, `event`, `theme`, `project` or `digest`. Notes only.

**Window** - a digest's coverage, `from` and `to`. Digests only; the search
header's coverage check reads it.

## Storage

**Ledger** (`.strata/ledger.db`) - durable, never dropped. Ids, versions and
anchor identity. The only irreplaceable per-project state besides `notes/`.

**Index** (`.strata/cache/index.db`) - derived and disposable. Rebuild is
`rm -rf .strata/cache/`, then sync replays the ledger.

**Header** - what search returns before hits: totals, estimated tokens, counts by
month, digests already covering the window, suggested chunks. What makes fan-out
decisions cheap and deterministic.

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

# Knowledge Strata - design

Written 2026-09-08 from the founding design conversation, revised the same day
after an architecture review of the first draft against the memoria code it
reuses. A from-scratch rethink of memoria
(github.com/JddAndrewLauren/memoria). Where this document contradicts a
memoria ADR or plan section, this document wins.

## Two principles, in order

1. Persistent, accessible memory across a large corpus without overloading
   the context window.
2. As simple and invisible to the user as possible.

Anything that adds a user-facing concept, a tool, or a UI surface must justify
itself against both. The default answer is no.

## The user's experience

Point the agent at a corpus. Optionally point it at an existing manuscript.
Open a regular Claude Code session and ask anything from "write the next
chapter, stopping at date Y" to "what did my notes say about Dave in
November 2013". Nothing in the flow mentions indexes, sessions, subjects,
tokens or modes.

Setup is one command per project, run in the folder that will hold it:

    strata init --corpus <path> [--corpus <path> ...] [--manuscript <path>]

It writes `.strata/config.yaml`, registers the server in that folder's
`.mcp.json`, allowlists the tools and the skill's commits in
`.claude/settings.json`, makes the folder a git repository if it is not one,
creates `notes/project.md`, installs the skill and the reader agent at user
level, and runs the first index. Run again with flags it replaces the paths;
run bare in an initialized folder it is the refresh (re-sync, rewrite the
user-level files). First indexing reports progress and interruption/recovery state.
Until it completes, every tool reply declares `indexing: incomplete`; partial
results never imply a complete archive or earn digest coverage. Installation,
updates and recovery on macOS and Windows require the acceptance demonstrations
in `docs/acceptance.md`. The full contract is on wayfinder #7. After that the user types
`claude` in that folder, or opens it in the Claude Desktop app's Code tab,
which is the same runtime (decided 2026-09-08, see the research note
`docs/research/claude-desktop-hosting.md`).

## Multi-project

A project is a folder. The tool serves any number of them.

- **Per project:** `.strata/config.yaml` (the corpus roots and
  manuscript path the user typed, plus optional `chunk_tokens`, default 80,000;
  init writes only paths), `.strata/ledger.db`
  (durable: ids, versions, anchor identity - never dropped),
  `.strata/cache/index.db` (derived, disposable), `notes/`, `.mcp.json`, and
  a `.claude/settings.json` allowlist so the two tools and the skill's
  commits never prompt. The folder is a git repository the user never
  manages: `init` creates it if absent, `.gitignore` holds only
  `.strata/cache/`, and the skill commits at the end of each task (decided
  2026-09-08, wayfinder #7). Opening a Claude Code session in the folder is
  what selects the project; the server is started by that folder's
  `.mcp.json` with the folder as its argument.
  There is no "current project" concept and no project parameter on any tool.
- **Per user:** the skill, the reader agent definition, and a conversion and
  embedding cache at `~/.strata/cache/store.db` keyed by raw-unit content
  hash plus converter id (text) plus embedding model id (vectors), with the
  embedder's model files beside it. Everything under `~/.strata/cache/` is
  disposable.
  Normalizing a file and embedding its paragraphs are functions of immutable
  bytes, so a second project over the same archive pays nothing for them.
- **Shared between projects:** a corpus, by listing the same root in both
  configs. Nothing else. Notes are per project; a person's note in one book
  is not the other book's.

Rejected: one user-level server with a project parameter on every call. It
adds a concept to every tool for a distinction the working directory already
makes.

Not built until asked for: querying two projects in one session; shared
notes across projects.

## The shape

One local MCP server with two read tools, over one index fed by three
adapters, plus one setup command, one skill and one agent definition. Claude
Code is the agent, the editor and the session record. The only thing it
cannot do cheaply is see inside a large heterogeneous archive, so that is the
only thing the server does.

### Module map

    normalizer   raw unit -> text paragraphs      (converters by suffix)
    dating       raw unit -> date                 (all date knowledge)
    refs         parse/render refs by kind        (no dependencies)
    corpus       folder -> Records                (three adapters)
    index        sync(Records); search(); read()  (owns freshness, ranking,
                                                   the header)
    server       two MCP tools over index
    cli          strata init, strata index
    skill        the workflow, read by the session
    agent        the reader, cheap model, fixed digest shape

Each is a module with one interface. Depth is measured against that
interface; anything not named there is internal.

### The Record interface

Everything the index knows arrives as a Record:

    ref         source anchor or live artifact reference, see Refs
    kind        source | note | manuscript
    type        note only: the note's folder under notes/ (open vocabulary;
                project and digest are reserved)
    date        ISO date or empty, with confidence: exact | inferred | unknown
                and granularity: day | month | year (partial dates store the
                first of their period; filters use period overlap)
    date_text   the source's own wording, for display
    title       extractive gist: subject line, first sentence, heading
    paragraphs  text, in order
    aliases     note only
    window      note only: optional from, to
    corpus_revision  digest only: optional server-issued source revision
    coverage_complete  digest only: optional boolean; true only for a completed
                       source-wide window reading at that revision

Three adapters produce Records, and their existence is what makes the seam
real:

- **Sources adapter.** Walks the corpus roots, calls the normalizer for text
  and the dating module for the date, allocates stable ids through a manifest
  ledger (memoria ADR-0006 survives). Emits kind `source`.
- **Notes adapter.** Reads `notes/**/*.md` as they are; the type is the
  folder. Owns the frontmatter contract: `aliases`, `window`, `corpus_revision`,
  `coverage_complete`. Validates it; a
  note that fails is indexed with a warning, not dropped. Emits kind `note`.
- **Manuscript adapter.** Reads the manuscript folder as it is, heading-aware.
  Emits kind `manuscript`.

The index takes Records and nothing else. It never learns what a folder is,
and its tests hand it Records directly.

### Dating

The load-bearing module. Every promise about scope rests on it, and the
memoria code being reused has none: three of four converters hardcode an
empty date and the one that does not stores a verbatim header string that
cannot be compared.

Interface: raw unit (bytes, path, kind, converter metadata) in; ISO-sortable
date, confidence, and the verbatim wording out. Converters do not touch date
fields.

Internal strategies, tried in order, first hit wins, each an internal seam
tested through the one interface: transport headers (email `Date`, then
`Received`), the unit's first line or leading heading, path patterns
(`2013-11-04-journal.md`, `2013/November/`), and document metadata (docx
core properties, pdf info; never `exact`, scans `unknown`). The author's own
words about a date beat every non-email piece of metadata, filenames
included. Per-collection rules from config are deferred until a real archive
defeats these four. A date that no strategy produces is empty with confidence
`unknown`; no invented date, and an ambiguous all-numeric date keeps only its
year. Dates are a hint, not a gate: search never drops a record because it is
undated, and a hit never shows a partial date as a day. Sources run the whole
chain, manuscript chapters only first line and path, notes none.

One date now. A journal entry written on X about X-30 is the failure to
watch for; "about" is a second output the same seam can grow, not a field
to build first. Memoria's `recorded_date`, `event_date` and
`contemporaneous` collapse to this.

### Refs

Two shapes, decided by kind, one module with no dependencies (a clean slice
of memoria's `references.py` - see "What is reused", not the whole module).

- **Source refs are durable.** `SRC-000184 p17`. A source record is frozen
  at paragraph granularity: an anchor never changes its exact stored text.
  If the raw file changes or a converter is bumped, reconversion produces a
  new version silently; only exactly equal paragraph text keeps an anchor (including punctuation,
  capitalization and whitespace). Identical duplicates pair in document order.
  Changed or vanished paragraphs retire and their text is kept
  in the ledger, so old refs keep resolving to the frozen text they cited
  and `read` says when one is retired (wayfinder #15). There is no drift
  report artifact. Memoria's positional anchors that shift silently on edit
  are the failure this prevents.
- **Manuscript refs are positional.** `manuscript/ch24.md # heading`. Never
  finer, never stored in a note as if durable. The skill enforces this by
  instruction (cite by file and heading, quote an ambiguous heading rather
  than number it); nothing detects, and no `is_durable` predicate is built
  until something calls it (wayfinder #7).
- **Note refs are paths.** `notes/person/dave.md`. Paths name live artifacts;
  they do not freeze wording. All notes can be read through bounded pages.
- **Ranges traverse document order.** `p17-22` includes both live endpoint
  anchors and everything between them in current document order, regardless of
  numeric labels; `p17-` means the current tail. Missing/retired endpoints
  produce an explicit diagnostic, with retired text available by single anchor,
  rather than silently retargeting a range. Reversed endpoints are an error.
  Ranges locate live passages; cite individual anchors for exact persistent text.

Search emits the right shape per kind. Digest evidence cites source anchors;
notes and manuscript paths remain live context references.

### Index

One module, three responsibilities behind one interface.

**Freshness.** `sync(records)` diffs by content hash, upserts changed
records, embeds only changed paragraphs, deletes vanished ones. Whether it
runs at the start of every tool call or on a file watcher is an
implementation choice behind the seam; the interface promise is "fresh as
of this call". Rebuild is sync from empty. Memoria's drop-and-rebuild is not
reused.

**Search.** Hybrid: FTS5 and local CPU embeddings (fastembed
`bge-small-en-v1.5`, on by default, memoria ADR-0007 reversed), merged by
reciprocal rank fusion inside the module. Filters: date range, `who`
(expanded through note aliases), `kind`. Returns a **header** then hits:

    header  lexical_total; evidence_total; estimated tokens; counts by month;
            digests already covering the window; suggested chunks
            for the configured chunk budget
    hit     ref, date, kind, title, snippet in context

The header is what makes fan-out decisions cheap and deterministic. The
skill reads it and acts; it never counts, estimates or partitions in prose.
The revised contract below supersedes the original resolution on #10.
One hit per record and RRF k=60 with equal weights remain. `who` expands
matching note titles, stems and aliases into an FTS phrase predicate shared
by both branches; unknown dates pass, partial dates use period overlap.
Browse orders by date then ref, unknown last; query results order by fused
relevance with stable ref ties.

**Evidence and budgets.** A query evidence set is all lexical matching
paragraphs plus the top 200 semantic paragraphs under identical filters,
deduplicated into records. RRF ranks this set; lexical results are not truncated
at 200. `lexical_total` explicitly counts lexical records and paragraphs;
`evidence_total` counts the union. `tokens`, `by_month`, `undated`, `inferred`
and reader assignments describe full records in that same evidence set, never
lexical-only costs for hybrid results. Browse has no semantic cutoff and can
enumerate the entire filtered archive. Semantic recall remains relevance-limited,
not a completeness claim. Estimates use UTF-8 text bytes / 4 rounded up and are
explicitly approximate; the serialized reply estimate includes all metadata.

**Enumeration.** `search` returns optional opaque continuation cursors that
retain query, dates, who/alias expansion, kind, ordering and index revision.
100 query hits and 500 browse hits are maximum page sizes, not total limits.
Following continuations visits the entire evidence set once at a stable revision.
Headers paginate `by_month`, `covered` and `chunks` as well as hits; explicit
continuations identify unfinished lists, including pages with no hits. An
approximately 8,000-token budget covers the entire serialized reply, including
metadata, cursors and `reply_tokens`; pages may contain fewer than the hit cap.
Oversized metadata fields must themselves be bounded/paged without losing a
ref or making a required continuation inaccessible.

**Reader assignments.** `chunk_tokens` defaults to 80,000 and is independent
of the caller's capacity. The server partitions evidence records once in date/ref
order (unknown last), excluding only coverage-eligible source records. Oversized
days split into executable assignments, each carrying a `search` cursor, original
scope, record count and estimated full-read cost, not first/last bounding refs.
An oversized record may span bounded read segments across assignments; their
completion must be combined before that record is considered read. Neither
repeated undated records nor overlapping partial dates may be counted or assigned
twice within a plan. Hits and evidence totals remain whole. Ordinary enumeration
always permits rereading covered sources without deleting digests.

**Read.** `read(ref, cursor?)` returns verbatim text for any source, note,
manuscript file or heading section, with an optional opaque continuation.
The same whole-reply budget applies, including retired-anchor text and metadata.
Split at paragraph boundaries when possible, otherwise at Unicode character
boundaries within a paragraph. Fragments preserve every character, punctuation,
capital and whitespace; concatenating payloads and their preserved separators
exactly reconstructs the selected stored text. Transport labels are separate
from text payloads. Cursors retain the selection, document order and offset;
source anchors remain citations, cursors never are.

**Revisions.** Every cursor identifies an index revision. Any change to that
revision, including note edits or a cache rebuild, explicitly invalidates it;
never silently resume against changed ordering. Restart the original scope and
deduplicate by record ref; changed records must be reread, and partial text from
different revisions must never be concatenated. An interrupted or invalidated
assignment cannot claim complete coverage. When saving a batch of notes
invalidates pending cursors, obtain a fresh plan and reconcile completed source
readings by record at the unchanged corpus revision; do not reread finished
unchanged records merely because note persistence changed the index revision.

A separate, durable server-issued `corpus_revision` advances when source content,
membership (including deletion), conversion or dating changes. Note/manuscript
edits do not advance it. Start with conservative corpus-wide invalidation.
A cache-only rebuild preserves it when the source state is unchanged. Only
`notes/digest/` notes with valid `window`, `coverage_complete: true` and the
current `corpus_revision`, produced by complete source-wide window readings,
receive coverage credit. A source-wide reading has no query or who restriction,
uses kind source, includes unknown/overlapping dates and exhausts all pages.
Filtered, partial, legacy and stale digests remain searchable but suppress no
assignments. Completion of only one split assignment never certifies a window.
Coverage may suppress a record only if that reading included it; overlapping
windows alone do not establish inclusion for coarse/unknown dates. The server
must evaluate membership conservatively. `covered` reports eligible digests;
ordinary note search retrieves stale ones.

Derived state (`.strata/cache/index.db`) is disposable. Nothing in it is
preserved across a rebuild, and nothing expensive enough to regret losing is
stored there. Durable identity - source ids, versions, and which anchor maps to
which paragraph - is history rather than derivation, so it lives in
`.strata/ledger.db` and survives a rebuild (ADR-0001).

### Two tools

- `search(query, from, to, who, kind, cursor?)` - every filter optional. An empty
  query with a date range is a timeline browse.
- `read(ref, cursor?)`.

A cursor resumes its server-issued scope without new arguments; conflicting
arguments are rejected. Assignment cursors are accepted by `search`; read
continuations by `read`. Neither adds a third tool.

There are no write tools. The agent writes notes and manuscript with its own
Write/Edit tools; the index notices on the next call. Git is the write path.

### Summaries: gists are for recall, verbatim is for writing

The user almost always gets a summary; the phrasing of the question decides.
"What was I doing on date X" is answered with a synthesis; "show me the
journal entry from date X" is answered with the text. No flag, no mode.

Where summarization runs:

- Small result sets: the session reads and summarizes.
- Large result sets: a reader agent returns a digest (fan-out, below).
- Every hit carries an extractive title for scanning. The serialized reply
  budget determines how many hits fit on a page; no fixed cost for hundreds
  of arbitrary titles is assumed.

Gists are extractive only: subject line, first sentence, heading, computed
at index time for free. Model-written gists are deferred (see "after a real
failure"); the first draft of this design kept them and had nowhere
consistent to store them.

Rollups (a month, a person's arc, a theme across chapters) are not
precomputed. When the agent answers such a question it saves the answer as a
note with refs; the next question over that scope hits the note first.
Memory accretes from the questions actually asked.

### Scope is bounded by dates, history is carried by notes

"Chapter 24, stopping at Y" means every record between where chapter 23
ended and Y. That set is bounded by the window, not by corpus size. Threads
that began before the window are carried by the people and event notes,
which the scan surfaces and the agent reads instead of re-searching the
past.

Each chapter write updates the bounded current overview in `notes/project.md`:
dates covered, people active, threads opened or closed, themes touched, and
links to supporting notes and manuscript locations. Keep it at most 2,000 words,
moving older entries to searchable `notes/history/` notes before exceeding that
limit. Preserve links, citations, corrections and unfinished work. Ordinary note
loading uses `read` continuations and the current session budget.

### Notes frontmatter

A small, named interface, owned by the notes adapter, not a convention:

    aliases   list of strings; `who` filters expand through them, whatever
              the note's type
    window    from, to; optional on any note; the header's coverage check
              reads it from `notes/digest/` only

    corpus_revision  server-issued revision for eligible digests
    coverage_complete  true only after complete source-wide window reading

Four optional fields, validated in one place. Invalid or missing coverage
metadata grants no coverage credit. The skill teaches the agent to
write them; the adapter is the only code that parses them.

The note's **type is its folder**: `notes/<type>/<slug>.md`, singular. Two
names are reserved because code depends on them: `notes/project.md`, the one
project note the skill reads first (created by `init`), and `notes/digest/`,
whose files are named by window (`2001-06-01--2001-06-30.md`), with unique
suffixes for repeated scopes and descriptive names for open/undated scopes. The skill
teaches `person`, `event` and `theme` as a starting vocabulary and the agent
may coin others (`place`, `company`, `deal`). Nothing filters by type; search
surfaces every note by text and aliases alike.

Notes other than digests and the project note accrete from answers: after a
question or a chapter, the agent saves a note when the answer was about a
person, event, theme or other thing and none exists, or appends when one
does. The slug is the canonical name (the user's form if given, else the
fullest form the sources use) and is never renamed, because the path is a
ref. Aliases are seeded from the name forms seen while answering. Before a
note exists, `who` is a plain match on the string given.

### Fan-out: one cheap reader agent, one protocol

An agent definition (the **reader**) with Haiku as its default model and a
narrow job: given an executable assignment cursor, call search and read and return
a digest in a fixed shape. It never writes prose or touches the manuscript.
The skill overrides to Sonnet for reading that needs nuance (thematic
passes). Fan-out work never runs on the main session's model.

Digest shape, fixed for bounded reduction: what happened as
dated bullets with refs; who appears, one line per person; open threads and
changes of state; a short section of verbatim quotes worth having for
writing. Hard length cap.

Protocol, in the skill:

1. Search the scope, follow header continuations, and evaluate eligible digests
   against the question. Reuse supported findings, preserving omissions.
2. Read directly only when evidence cost fits remaining capacity after reserves;
   otherwise take executable reader assignments with original filters intact.
3. Run at most four readers concurrently. Follow all server continuations;
   interrupted/invalidated work remains explicitly incomplete.
4. Persist every returned digest and batch recovery state before starting another
   batch. Readers write nothing; the caller saves their reports, including gaps.
5. Reduce groups of at most four saved digests into cited summaries of at most
   1,200 words. Persist each reduction; recursively reduce groups of at most four
   when needed. Every level preserves child links, source citations, omissions,
   contradictions and incomplete-reading status. Reduction alone earns no coverage.
6. Synthesize from a bounded set of saved summaries, reading sources for exact
   wording and omitted detail. A completed aggregate may claim window coverage
   only after all original source-wide assignments finish at the current revision.

Use host-reported capacity when available, with a conservative 200k-token fallback;
do not assume a 1M session. Reserve capacity for existing conversation, output,
synthesis and host overhead. Track all incoming replies and reader reports, and
persist a recovery note before pressure requires compaction. If the host requires
the user to compact, explain that host limitation plainly. Fresh sessions load
the bounded project overview and linked recovery state, then targeted notes;
they must recover supported findings without rereading the entire archive.

Where the tuning numbers live: chunk budget in `.strata/config.yaml` (the
server computes chunks); in-session budget and concurrency cap as named
settings at the top of the skill; digest length cap in the agent definition.
Expect to adjust all of them after the first real corpus.

## What is reused from memoria

- The normalizer's converters (plain text, docx, pdf, email), stripped of
  date handling.
- The record schema, trimmed to the Record interface above.
- `references.py`, in part: about 80 of its 510 lines. It implements twelve
  reference kinds and eleven of them (`SES-`, `CLM-`, `SUB-`, `DEC-`, `RES-`,
  `CHP-`, `SEC-`, `CHG-`) are machinery left behind below. What survives is the
  `SRC-` grammar, the `anchor`/`split_anchor` pair as the single source of the
  anchor form, the parse-and-format-through-one-place discipline, and
  `_repository_path`'s refusals. It imports nothing from memoria and touches no
  filesystem, which is why the slice is clean.
- The manifest ledger for stable source ids.
- The FTS5 and sqlite-vec parts of the index (about 700 of its 1900 lines)
  and the embedder.
- The `read` and `search_text` tools (about 350 of the server's 1660 lines)
  and the rule behind them: search never returns whole documents, read never
  summarizes.

Not reused even though it looked reusable: `ingestion.py`, which imports
extraction and subjects to do its job; the index's rebuild path; the two
separate lexical and semantic search tools.

## What is deliberately left behind

- The subject system: subjects, entries, candidates, placements, relations,
  clusters, promotion, pin/exclude. Replaced by notes and by search with
  aliases from a person's note.
- The extraction pass. Themes are found when asked (semantic search plus
  fan-out) and remembered as notes, not pre-discovered.
- The audit and impact analysis as mechanisms. They are prompts the user can
  type because search and read make them answerable.
- The curator, the supplied-context ledger, sessions, settlements, the style
  pass, the grilling. The Claude Code transcript is the record.
- The React UI, the FastAPI layer, the gate walks.
- Embeddings by choice becomes embeddings by default.
- Recorded date, event date and contemporaneous become one date.

## The one invariant relaxed, stated plainly

Memoria's foundational rule was that every durable assertion must trace
structurally to a source, a turn or a commit. Here that becomes a convention:
notes cite refs, hits carry kind and date, digests are marked by type. This
is a real relaxation. It is accepted because the machinery that enforced it
structurally is most of what made memoria hard to use. What is kept
structurally is the part a convention cannot supply: source refs are
durable, so a citation written today resolves to the same text in ten years.

## What to add only after a real failure

- Model-written gists. If extractive titles prove too thin for scanning, the
  path is a committed `.strata/gists/` sidecar written by the reader agent
  and indexed by a fourth adapter, or an API-key pass at index time. Not a
  write tool.
- An "about" date beside the written date, if memoir-style sources make the
  single date mislead.
- A one-time background tagging pass over records (people, places) if
  lexical search plus aliases proves insufficient for entity questions.
- Cross-project queries or shared notes.
- Anything else.

## Acceptance before support claims

`docs/acceptance.md` is the cross-module memory and user-journey gate. The
revised contracts are settled implementation requirements; they are not test
results. Archive reconnaissance (#20) is required before claiming support for
the actual user's manuscript/source formats, including multi-entry files,
exports and images if present. Enron and invented fixtures do not prove that
support. Continue independently settled planning while the report is pending.

## Build order

1. refs and the Record interface, with tests.
2. dating, with a fixture corpus of paths and headers.
3. normalizer port, converters only.
4. corpus adapters: sources, notes, manuscript.
5. index: sync, then search with the header, then read.
6. server: two tools. cli: init and index.
7. skill and reader agent.

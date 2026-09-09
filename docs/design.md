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
user-level files). The full contract is on wayfinder #7. After that the user types
`claude` in that folder, or opens it in the Claude Desktop app's Code tab,
which is the same runtime (decided 2026-09-08, see the research note
`docs/research/claude-desktop-hosting.md`).

## Multi-project

A project is a folder. The tool serves any number of them.

- **Per project:** `.strata/config.yaml` (exactly the corpus roots and
  manuscript path the user typed, nothing else), `.strata/ledger.db`
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

    ref         durable or positional, see Refs
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
    window      digest only: from, to

Three adapters produce Records, and their existence is what makes the seam
real:

- **Sources adapter.** Walks the corpus roots, calls the normalizer for text
  and the dating module for the date, allocates stable ids through a manifest
  ledger (memoria ADR-0006 survives). Emits kind `source`.
- **Notes adapter.** Reads `notes/**/*.md` as they are; the type is the
  folder. Owns the frontmatter contract: `aliases`, `window`. Validates it; a
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
  the first time it is indexed: its paragraph text and anchors do not change.
  If the raw file changes or a converter is bumped, reconversion produces a
  new version with a drift report; old refs keep resolving to the frozen
  text they cited. Memoria's positional anchors that shift silently on edit
  are the failure this prevents.
- **Manuscript refs are positional.** `manuscript/ch24.md # heading`. Never
  finer, never stored in a note as if durable. The skill enforces this by
  instruction (cite by file and heading, quote an ambiguous heading rather
  than number it); nothing detects, and no `is_durable` predicate is built
  until something calls it (wayfinder #7).
- **Note refs are paths.** `notes/people/dave.md`. A note is small enough to
  read whole.

Search emits the right shape per kind, so a digest can only ever cite what
will still be there.

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

    header  total hits; estimated tokens; counts by month;
            digests already covering the window; suggested chunks
            for the configured chunk budget
    hit     ref, date, kind, title, snippet in context

The header is what makes fan-out decisions cheap and deterministic. The
skill reads it and acts; it never counts, estimates or partitions in prose.

**Read.** `read(ref)` returns a paragraph, a range or a whole record,
verbatim, capped, with a ref to continue.

Derived state (`.strata/cache/index.db`) is disposable. Nothing in it is
preserved across a rebuild, and nothing expensive enough to regret losing is
stored there. Durable identity - source ids, versions, and which anchor maps to
which paragraph - is history rather than derivation, so it lives in
`.strata/ledger.db` and survives a rebuild (ADR-0001).

### Two tools

- `search(query, from, to, who, kind)` - every filter optional. An empty
  query with a date range is a timeline browse.
- `read(ref)`.

There are no write tools. The agent writes notes and manuscript with its own
Write/Edit tools; the index notices on the next call. Git is the write path.

### Summaries: gists are for recall, verbatim is for writing

The user almost always gets a summary; the phrasing of the question decides.
"What was I doing on date X" is answered with a synthesis; "show me the
journal entry from date X" is answered with the text. No flag, no mode.

Where summarization runs:

- Small result sets: the session reads and summarizes.
- Large result sets: a reader agent returns a digest (fan-out, below).
- Every hit carries a title, the extractive gist, so a scan of hundreds of
  hits costs a few thousand tokens.

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

Each chapter write appends one paragraph to the project note: dates covered,
people active, threads opened or closed, themes touched. The project note is
the most important file in the system; updating it is part of writing a
chapter, not cleanup.

### Notes frontmatter

A small, named interface, owned by the notes adapter, not a convention:

    aliases   list of strings; `who` filters expand through them, whatever
              the note's type
    window    from, to; optional on any note; the header's coverage check
              reads it from `notes/digest/` only

Two optional fields, validated in one place. The skill teaches the agent to
write them; the adapter is the only code that parses them.

The note's **type is its folder**: `notes/<type>/<slug>.md`, singular. Two
names are reserved because code depends on them: `notes/project.md`, the one
project note the skill reads first (created by `init`), and `notes/digest/`,
whose files are named by window (`2001-06-01--2001-06-30.md`). The skill
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
narrow job: given a scope or a list of refs, call search and read and return
a digest in a fixed shape. It never writes prose or touches the manuscript.
The skill overrides to Sonnet for reading that needs nuance (thematic
passes). Fan-out work never runs on the main session's model.

Digest shape, fixed so eight of them merge mechanically: what happened as
dated bullets with refs; who appears, one line per person; open threads and
changes of state; a short section of verbatim quotes worth having for
writing. Hard length cap.

Protocol, in the skill:

1. Search the scope. The header reports what is already digested; skip it.
2. If the header's estimated size fits the in-session budget, read directly
   and stop.
3. Otherwise take the header's suggested chunks.
4. Tell the user how many readers are about to run and why, then spawn them
   in parallel with a concurrency cap.
5. Save each digest as a note of type `digest` with its window and refs.
6. Synthesize or write from the digests, reading verbatim only where the
   writing needs actual wording.

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

## Build order

1. refs and the Record interface, with tests.
2. dating, with a fixture corpus of paths and headers.
3. normalizer port, converters only.
4. corpus adapters: sources, notes, manuscript.
5. index: sync, then search with the header, then read.
6. server: two tools. cli: init and index.
7. skill and reader agent.

---
name: strata
description: Work with this project's archive, notes and manuscript through the strata search and read tools. Use for any request that draws on the archive - recalling, summarizing, quoting, or writing from it - and whenever a task ends and the notes or manuscript changed.
---

# Strata

This folder is a strata project: `notes/`, optionally a manuscript, and two MCP
tools over an archive too large to read. The user sees none of the machinery.
Keep internal machinery invisible during ordinary work. Explain progress, missing
evidence, recovery and any host-required user action plainly when necessary.

## Settings

Use known host capacity; otherwise assume 200,000 tokens. These are conservative
initial settings to validate, not measured host guarantees.

| setting | value | meaning |
|---|---|---|
| `capacity_fallback` | 200000 | context capacity when the host gives no usable figure |
| `reserve` | at least 50% of capacity | space for conversation, output, synthesis and host overhead; subtract already occupied context too |
| `direct_read_max` | min(50000, remaining unreserved capacity) | maximum estimated evidence text to read directly |
| `reader_concurrency` | 4 | maximum readers running at once |
| `reduction_fan_in` | 4 | maximum saved child digests/summaries loaded per reduction |
| `summary_max_words` | 1200 | cap per persisted reduction |
| `project_max_words` | 2000 | cap for the current project overview |

Apply the capacity calculation separately to each reader. An assignment may take
several reader invocations: persist incomplete progress and resume its server
continuations in a fresh reader. The tools have no per-call chunk-size control;
do not invent one or silently change project config. If restarting cannot make
progress within capacity, preserve the unfinished scope and explain the limit.
Persist before the next batch or operation
would consume the reserve, and sooner on any host context warning.

## The two tools

- `search(query, from, to, who, kind, cursor?)` - all optional; an empty
  query browses the archive. A cursor alone resumes the exact server-issued
  scope; conflicting arguments are rejected.
- `read(ref, cursor?)` - a paragraph, document-order range, whole source,
  note, manuscript file or heading section. A read cursor alone resumes the
  same selection and offset. Both tools cap their entire serialized reply,
  including metadata, at approximately 8,000 tokens.

Act on server estimates and assignments; never invent partitions or refs.

```text
indexing        complete
index_revision  <opaque index revision>
corpus_revision <opaque source revision>
lexical_total   0 records, 0 paragraphs
evidence_total  12 records, 18 paragraphs
tokens          ~90000
by_month        <bounded rows; continuation if unfinished>
undated         3
inferred        0
covered         <eligible digest rows; continuation if unfinished>
chunks          <scope, record count, ~cost, executable assignment cursor>
shown           12 of 12 evidence records
continuations   <opaque cursors for unfinished lists/hits, or none>
reply_tokens    ~5400
```

- For queries, evidence is all lexical matches plus the top 200 semantic
  paragraphs under identical filters, deduplicated into records. `tokens`
  estimates full evidence records, including semantic-only results. Lexical
  counts describe only lexical matches. Semantic results are relevance-limited;
  only browse supports a claim to have enumerated the whole archive.
- 100 query hits and 500 browse hits are maximum page sizes. Follow all relevant
  continuations, including month, coverage and assignment lists; empty hit pages
  do not imply enumeration is done. No narrowing dates to work around caps.
- `covered` grants credit only to complete source-wide digests at the current
  corpus revision. Read limitations before reuse. Search remains able to
  enumerate covered sources when a question needs omitted detail.
- `chunks` contain executable assignment cursors, including split days or
  oversized records. Give the reader their recorded filters and scope as context;
  execute the cursor alone, without reconstructing tool arguments.
- Unknown dates remain included; partial dates filter by period overlap.
  Deduplicate record refs across restarted or overlapping plans. Reread changed
  records. An invalidated cursor requires restarting its original scope;
  never combine text fragments from different revisions.
- `read` fragments preserve exact text, even inside an oversized paragraph.
  Follow read continuations to completion when the task needs the whole
  selection. Concatenate payloads with their preserved separators, without
  transport labels or added/trimmed whitespace. Cursors are never citations.
- If `indexing` is incomplete, tell the user preparation is still underway.
  Partial results cannot establish completeness or digest coverage.
- Account for `reply_tokens`, returned reader digests, generated summaries and
  conversation. Reply sums alone do not measure actual context occupancy.

A source hit cites its best-matching paragraph; use its bare record id for the
whole record. `p17` is an id, not a position. A note path names live text.

## Input

Input is the primary activity. For synthesis and writing, gather relevant
context across corpus, notes and manuscript. For direct retrieval or a narrow
factual question, locate and read the requested evidence; expand only when
ambiguity or conflicting evidence requires it. A supplied ref can go straight
to `read`. Retrieval alone requires no note update or manuscript reading.

- **Notes.** For synthesis and writing, read `notes/project.md`;
  it is the bounded current overview. Follow linked recovery and history notes
  relevant to the task through `read` continuations, within capacity. Search `kind: note` for the people,
  events and themes the request names. Notes and digests are starting points,
  not final authority: recheck their sources when evidence conflicts, sources
  changed or the question needs detail they omit.
- **Corpus.** Bound with `from`/`to` whenever the request allows; `who` for a
  person (it expands through their note's aliases). For synthesis, search both
  ways: a timeline browse of the window for what happened, and word searches for the
  specific things asked about. When a scope has no natural bound, ask one
  question rather than guess.
- **Manuscript.** For synthesis and writing, search `kind: manuscript` for
  what the book already says about the scope. Source evidence can contradict
  it: surface the discrepancy and correct outdated notes with refs. Change
  manuscript text only within the requested editing scope. If sources disagree,
  preserve the disagreement rather than inventing a resolution.
- **Read or fan out.** If a header's `tokens` fits `direct_read_max`, read what
  you need. Otherwise fan out (below) and read from the digests.
- **Verbatim only from `read`.** Snippets, titles and digest bullets locate
  text; they are never quoted.

## Output

Output is anything produced from the input: an answer, a note, manuscript
text. Use the reading depth appropriate to the request above.

- **The neighbouring manuscript before editing it.** `read` what precedes
  the point the work touches
  (the previous chapter or section, at least its tail) and what follows it,
  and note the heading style and file naming. New text continues the book and
  never starts over. Preserve continuity without copying factual errors.
- **Shape follows phrasing.** A question about what happened gets a synthesis;
  a request to see something gets the text. No flags, no asking which.
- **Write with your own tools.** Write and Edit, into the manuscript or
  `notes/`; the server has no write tools and needs none. Quote sources only
  from `read` output.
- **Record.** Every manuscript change updates
  `notes/project.md`: dates covered, people active, threads opened or closed,
  themes touched, file and heading. Record new supported facts or corrections
  about a thing in its note (see *Notes*). Commit once at task end.

## Fan-out

Readers are `strata-reader` subagents: they return a digest in a fixed shape
and write nothing. Their enumeration procedure and digest template are in the
installed `strata-reader` agent definition (`~/.claude/agents/strata-reader.md`).

1. Search the scope and follow planning continuations. Assess eligible coverage
   against the question; stale summaries may still contain supported findings
   but cannot suppress new reading.
2. Read directly only if evidence cost fits the remaining unreserved budget.
   Otherwise pass each executable assignment cursor, its original query/date/
   who/kind scope, revisions, estimated cost and question to a reader. On restart,
   supply relevant bounded completion records from saved recovery notes; readers
   skip only verified completed work at the unchanged corpus revision. Source
   completion cannot justify skipping changed notes or manuscript text.
   Use ordinary search continuations
   to reread covered spans; coverage never removes hits.
3. Tell the user how many readers will cover what span and why. Run at most
   `reader_concurrency` at once. Use the reader default model; override to
   Sonnet when themes, tone or intent require nuance.
4. Readers follow server continuations and preserve assignment scope. Persist
   every digest verbatim, including incomplete reports, under
   `notes/digest/<from>--<to>[-N].md` (use a unique descriptive scope for undated
   or open-ended assignments). Wait for the batch's readers to finish before
   writing notes, so these writes do not invalidate other readers mid-page.
   Save the batch before starting another.
5. Update a linked recovery note with the task, original filters, index/corpus
   revisions, completed and pending assignments, interrupted cursors, digest
   paths and gaps. Cursors are hints for resumption; validate them before reuse.
   If invalidated, restart that scope and reconcile record completion at the
   new revision. Keep detailed completion records in bounded linked recovery
   notes; keep only the active slice and links in the recovery overview. Record
   whole-record versus segment completion separately, retaining server-issued
   selection details. Never invent a completion list or link a reader did not
   supply. Batch note writes also invalidate pending index cursors: get
   a fresh plan, retaining verified completed source reads when corpus revision
   is unchanged. Never promote interrupted work to complete coverage.
6. Reduce at most four saved digests at a time to a cited summary of at most
   1,200 words in `notes/summary/`. Save each reduction before processing the
   next group. Recursively reduce at most four children until the final set
   fits capacity. Every level preserves child links, source refs, omissions,
   contradictions and incomplete-reading status. Keep the detailed gaps in
   linked notes when lengthy; never hide their existence in the parent.
7. A reader digest may declare coverage only for an entire source-wide window
   completed at the current corpus revision. Split assignments omit coverage
   fields. The caller may save an eligible aggregate digest only after verifying
   all source-wide assignments, including unknown/overlapping dates and every
   read page, completed at one current revision. Reduction itself proves nothing.
8. Work from supported saved findings; use `read` for exact wording, missing
   detail and conflicts. Silence in a digest is never evidence of absence.

## Notes

`notes/<type>/<slug>.md`. The folder is the type: singular, open vocabulary.
`person`, `event`, `theme` are the starting set; coin another singular word
when none fits. Reserved: `notes/project.md` and `notes/digest/`.

- **When.** After a task establishes new supported facts about a thing:
  create if absent, append if present. Skip duplicates and retrieval-only
  answers. Mark superseded claims when correcting them and cite the evidence;
  do not leave incompatible bullets as equally current. Nothing speculative.
- **Slug.** The canonical name: the user's form if given, else the fullest
  form the sources use. Never renamed; the path is a ref.
- **Frontmatter.** `aliases` contains observed name forms. `window` (`from`,
  `to`) is optional on any note. Eligible digests additionally carry the exact
  server-issued `corpus_revision` and `coverage_complete: true`. Only complete
  source-wide window readings at the current revision earn coverage. Filtered,
  split, incomplete, legacy or stale digests earn none. Never invent a revision
  or copy a new revision onto an old digest. The user never edits frontmatter.
- **Project overview.** Keep `notes/project.md` within 2,000 words. Move older
  entries to searchable `notes/history/` before exceeding the cap, retaining
  citations, correction history and links. Keep current threads and a recovery
  note link in the overview. Load ordinary notes through `read` pages too.
- **Body.** Dated bullets with refs. Short and factual; a note is memory, not
  prose.

```markdown
---
aliases: [Dave, D. Fuller, dave.fuller@example.com]
---
# Dave Fuller

- 2001-06-14  Moved the Friday schedule after the Portland call. (SRC-000184 p17)
```

## Citing

- **Sources**: `SRC-000184 p17`, for exact persistent wording. A `p17-22` run traverses
  current document order between live endpoints, not numeric labels; cite
  individual anchors when preserving exact quotations across future edits.
- **Dates**: preserve displayed granularity and confidence. An inferred day
  remains an inferred day; an inferred month or year (`2013-11 (folder)`,
  `2013`) never becomes an invented day. `undated` stays undated.
- **Manuscript**: file and heading, `manuscript/ch24.md # The Letter`, in the
  project note and nowhere finer. A repeated heading is quoted, never numbered.
- **Notes**: the bare path.
- **Retired anchors**: `read` shows the text the citation meant; use it. For
  current wording, search afresh and cite the new anchor. Never substitute a
  neighbour.

## Reading budget

Check after tool replies and between reader batches. Use the settings above
against available capacity; a reply sum excludes conversation and generated
text and is not a safe standalone trigger. Save reports, reductions and recovery
state before pressure requires compaction, not after consuming the reserve.

If the host cannot compact automatically and requires a user command, explain
that host limitation once, then offer:

```text
/compact Keep the project overview and recovery-note path, saved digest/summary links, current task, original filters and revisions, source citations, and incomplete reading gaps.
```

Do not claim compaction happened until it does. In a fresh session, read the
bounded project overview and linked recovery note, then targeted saved summaries
and notes. Recover supported prior findings without rereading the whole archive;
check current corpus revision before accepting coverage or resuming cursors.

## Ending a task

Commit once, silently on success, only when this task changed project artifacts.
Inspect status and the diff; stage and commit only this task's changes using
explicit paths. Preserve unrelated changes, including anything already staged;
if ownership cannot be separated, leave the work intact and report why it was
not committed. Skip an empty commit. If committing fails, preserve the edits
and report the failure; do not discard work or claim it was saved in history.

---
name: strata
description: Work with this project's archive, notes and manuscript through the strata search and read tools. Use for any request that draws on the archive - recalling, summarizing, quoting, or writing from it - and whenever a task ends and the notes or manuscript changed.
---

# Strata

This folder is a strata project: `notes/`, optionally a manuscript, and two MCP
tools over an archive too large to read. The user sees none of the machinery.
Never mention indexes, tokens, chunks, sessions or modes; the two exceptions
are marked below.

## Settings

Sized for a 1M-token session. Adjust after the first real corpus.

| setting | value | meaning |
|---|---|---|
| `direct_read_max` | 100000 | largest `tokens` figure in a search header you read directly rather than fanning out |
| `reader_concurrency` | 4 | readers running at once during a fan-out |
| `compact_at` | 850000 | approximate reading budget since the last compaction or fresh session |

## The two tools

- `search(query, from, to, who, kind)` - every argument optional. An empty
  query with a date range is a timeline browse. Reply: a **header**, a blank
  line, then hits.
- `read(ref)` - a paragraph (`SRC-000184 p17`), a run (`p17-22`) or a whole
  record (`SRC-000184`, `notes/person/dave-fuller.md`, `manuscript/ch03.md`).
  Verbatim, capped, ending in a continuation ref (`SRC-000184 p31-`) when cut;
  pass that ref back as is.

Act on the header; never count, estimate or partition in prose.

```
total      1140 records, 3877 paragraphs
tokens     ~310000
by_month   2001-06  612 records  ~168000
           2001-07  528 records  ~142000
undated    3
inferred   0
covered    notes/digest/2001-06-01--2001-06-30.md  2001-06-01..2001-06-30
chunks     2001-07-01..2001-07-16   271 records  ~76000
           2001-07-17..2001-07-31   257 records  ~66000
shown      100 of 1140
reply_tokens ~5400
```

- `tokens`: the cost of reading every match in full. Compare with
  `direct_read_max`.
- `covered`: existing summaries, not proof that the question is answered.
  Their days are already left out of `chunks`, but not search hits or totals.
  Read each relevant digest's scope and limitations. Reuse supported facts;
  search and read sources for omitted detail, changed sources or a new question.
  For excluded days that need rereading, search those dates with the original
  filters and follow the reader's enumeration procedure; do not rely on
  `chunks` to restore them or remove existing digests to force a new plan.
- `chunks`: present only when the scope is too big for one reader. Rows are
  `from..to  records  ~tokens`, plus `first..last` bounding refs when a single
  day was split.
- `undated`, `inferred`: how soft the window's dates are. Dates are a hint,
  not a gate; undated records stay in.
- `shown`: fewer shown than total means incomplete retrieval, not proof that
  omitted hits are irrelevant. Narrow dates for enumeration; an overflowing
  single day may be unreachable with these tools (see *Fan-out*).
- `reply_tokens`: ends every reply. Sum replies entering this session across
  tasks; do not include readers' private tool replies. See *Reading budget*.

A hit is one record: ref, date, kind, title, `(n matches)`, and on query
searches one snippet line. Its ref is the best paragraph's anchor: `read` it for
the paragraph, the bare id for the record. `p17` is an id, not a position.

## Input

Input is the primary activity. For synthesis and writing, gather relevant
context across corpus, notes and manuscript. For direct retrieval or a narrow
factual question, locate and read the requested evidence; expand only when
ambiguity or conflicting evidence requires it. A supplied ref can go straight
to `read`. Retrieval alone requires no note update or manuscript reading.

- **Notes.** For synthesis and writing, read `notes/project.md`;
  it is the running memory of the project. Search `kind: note` for the people,
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
- **Record.** Every manuscript change appends one paragraph to
  `notes/project.md`: dates covered, people active, threads opened or closed,
  themes touched, file and heading. Record new supported facts or corrections
  about a thing in its note (see *Notes*). Commit once at task end.

## Fan-out

Readers are `strata-reader` subagents: they return a digest in a fixed shape
and write nothing. Their enumeration procedure and digest template are in the
companion [reader definition](../agents/strata-reader.md).

1. Search the scope. Assess `covered` against the question, including each
   digest's scope, omissions and any subsequent source changes.
2. If `tokens` fits `direct_read_max`, read directly and stop.
3. Otherwise take `chunks` as they stand, one reader per row, passing the row
   verbatim (with `first..last` when present), plus the original `query`, `who`
   and `kind` filters. For excluded days needing rereading, assign their date
   range and the same filters for enumeration, without inventing chunk totals.
4. **Tell the user** (first exception): one sentence, how many readers over
   what span, and that the span is more than one sitting holds. Spawn them in
   parallel, at most `reader_concurrency` at a time, each with the row and the
   question if there is one. Pass `model: sonnet` when the pass needs nuance -
   themes, tone, intent - otherwise the reader's default.
5. Readers preserve filters and narrow dates when results are truncated.
   These tools have no pagination or bounding-ref search arguments. If a
   single day still overflows, bounds are unreachable, or reading cannot
   finish, accept an explicitly incomplete report; never claim full coverage.
   Explain the missing evidence plainly to the user. Do not repeatedly retry
   the same scope or fabricate refs from numeric IDs.
6. Save each digest verbatim as `notes/digest/<from>--<to>.md`; use the next
   unused suffix (`-2`, `-3`, ...) for another over the same window. Only a
   complete, unfiltered whole-window reading may declare `window` frontmatter.
   Filtered, bounded and incomplete digests retain their scope in the body.
7. Work from supported digest facts; `read` for wording, missing detail and
   contradictions. A digest's silence never establishes that nothing happened.

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
- **Frontmatter.** Two optional fields, nothing else. `aliases`: the name
  forms actually seen (full name, first name, email address, initials), seeded
  at creation, added to later. `window` (`from`, `to`): optional; on digests,
  allowed only after complete, unfiltered whole-window reading. It records
  what was read, not exhaustive retention or permanent freshness. Only digests
  count as coverage. The user never edits frontmatter.
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

- **Sources**: `SRC-000184 p17`, or `p17-22` for a run. Durable.
- **Dates**: `exact` as a day. `inferred` at its own granularity - `2013-11
  (folder)`, `2013` - never as a day. `undated` stays undated.
- **Manuscript**: file and heading, `manuscript/ch24.md # The Letter`, in the
  project note and nowhere finer. A repeated heading is quoted, never numbered.
- **Notes**: the bare path.
- **Retired anchors**: `read` shows the text the citation meant; use it. For
  current wording, search afresh and cite the new anchor. Never substitute a
  neighbour.

## Reading budget

Check after tool replies and between reader batches, not just at task end.
The reply sum is an approximate reading budget, not actual context occupancy:
it excludes other conversation and generated text. Use a host context warning
or known remaining capacity to advise compacting earlier. Retain the sum across
tasks; reset only after compaction actually occurs or a fresh session begins.

**Compaction** (second exception). When the sum passes `compact_at` or the host
signals pressure, say once that the session has read a lot of the archive and
give the exact line before taking on more reading:

```
/compact Keep the project note, the digests written this session, every ref cited so far, and outstanding reading gaps.
```

Or suggest a fresh session if the task is finished.

## Ending a task

Commit once, silently on success, only when this task changed project artifacts.
Inspect status and the diff; stage and commit only this task's changes using
explicit paths. Preserve unrelated changes, including anything already staged;
if ownership cannot be separated, leave the work intact and report why it was
not committed. Skip an empty commit. If committing fails, preserve the edits
and report the failure; do not discard work or claim it was saved in history.

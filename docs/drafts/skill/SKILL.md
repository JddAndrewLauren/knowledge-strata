---
name: strata
description: Work with this project's archive through the strata search and read tools. Use whenever the user asks anything about the corpus, the notes or the manuscript - "what did my notes say about X in November", "show me the entry from date Y", "write the next chapter, stopping at date Z", "who was I dealing with that spring" - and whenever a task ends and its notes and manuscript changes need committing.
---

# Strata

You are working inside a strata project: a folder with `notes/`, optionally a
manuscript, and two MCP tools, `search` and `read`, over an archive that is
too large to read. Nothing here is the user's business. Never mention indexes,
tokens, chunks, sessions or modes to them; the two exceptions are marked below.

## Settings

Adjust after the first real corpus. These size *this* session's model, assumed to
have a 1M-token window; the server's `chunk_tokens` sizes the reader and is a
different number.

| setting | value | meaning |
|---|---|---|
| `direct_read_max` | 60000 | largest `tokens` figure in a search header you read directly rather than fanning out |
| `reader_concurrency` | 4 | readers running at once during a fan-out |
| `compact_at` | 600000 | summed `reply_tokens` across the task at which you advise compacting |

## The two tools

- `search(query, from, to, who, kind)` - every argument optional. An empty
  query with a date range is a timeline browse. The reply is a **header**, a
  blank line, then hits.
- `read(ref)` - a paragraph (`SRC-000184 p17`), a range (`p17-22`), or a whole
  record (`SRC-000184`, `notes/person/dave-fuller.md`,
  `manuscript/ch03.md`). Verbatim, capped at about 8,000 tokens, ending in a
  continuation ref (`SRC-000184 p31-`) when cut. Pass that ref back as is.

Read the header before anything else and act on it. Never count, estimate or
partition in prose; the header has already done it.

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

- `tokens` is the cost of reading every matched record in full. Compare it
  with `direct_read_max`.
- `covered` lists digests already written over part of the range. Days inside
  them are already left out of `chunks`. Read the digest instead of the sources;
  search a covered span explicitly only when the digest does not fit the
  question.
- `chunks` appears only when `tokens` exceeds the reader's budget. Each row is
  `from..to  records  ~tokens`, with `first..last` bounding refs when one day was
  too big and was split.
- `undated` and `inferred` say how soft the window's dates are. Dates are a
  hint, not a gate: undated records stay in the results.
- `shown 100 of 1140` on a query means the tail was cut as noise. `shown 500 of
  N` on a browse means narrow the range; the 500-row browse exists for readers
  working a chunk, so keep your own browses narrow.
- `reply_tokens` ends every `search` and `read` reply. Keep a running sum for
  the task.

A hit is one record: ref, date, kind, title, `(n matches)` when several
paragraphs matched, and on query searches one snippet line. The ref is the
best-matching paragraph's anchor; `read` it for the paragraph, or `read` the
bare id for the whole record. `p17` is an id, not a position.

## Answering a question

The phrasing decides the shape of the answer. "What was I doing in June" gets a
synthesis; "show me the entry from June 14th" gets the text. No flags, no
asking which.

1. Search with the narrowest filters the question gives: a date range, `who`
   for a person (it expands through their note's aliases), `kind` when the
   user named notes or the manuscript.
2. If a note answers the question, read it first. Notes are hits like any
   other; a `who` search surfaces the person's own note. A digest over the
   range is the summary you would otherwise write.
3. If `tokens` fits `direct_read_max`, read the hits you need and answer.
   Otherwise run the fan-out below.
4. After answering, update the notes (see *Notes*), then commit.

## Fan-out

For a scope too large to read in this session. Readers are `strata-reader`
subagents; they return a digest in a fixed shape and never write anything.

1. Search the scope. Skip whatever `covered` already digests.
2. If `tokens` fits `direct_read_max`, read directly and stop here.
3. Otherwise take the header's `chunks` as they stand: one reader per row.
   Give a reader the row verbatim, including `first..last` when present; a
   reader given a split day searches that day and reads between the bounds in
   timeline order.
4. **Tell the user** (first exception): one sentence naming how many readers
   are about to run and over what span, and why - the span is larger than one
   sitting can hold. Then spawn them in parallel, at most `reader_concurrency`
   at a time, each with the chunk row and the question if there is one.
   Default model is the reader's own (Haiku). Pass `model: sonnet` for passes
   that need nuance: themes, tone, what someone meant rather than what they
   did.
5. Save each digest as `notes/digest/<from>--<to>.md`, the reader's output
   verbatim. A second digest over the same window gets `-2`.
6. Synthesize or write from the digests. `read` verbatim only where the
   writing needs the actual wording, using the refs the digests carry.

## Writing a chapter

"Write chapter 24, stopping at Y" means everything between where chapter 23
ended and Y. The window bounds the work, not the size of the archive.

1. Read `notes/project.md` whole. Its last paragraph says where the previous
   chapter stopped: dates covered, people active, threads left open.
2. If a manuscript is configured, read the tail of the previous chapter (its
   last two or three hundred words) and note its heading style and file
   naming. New chapters follow both.
3. Fix the window: the day after the last covered date, to Y. If the user gave
   no stopping date, ask for one - a single question, then proceed.
4. Timeline-browse the window (empty query, `from`, `to`). Fan out if the
   header says to.
5. Read the notes of the people the header, hits or digests surface. Threads
   that began before the window live in those notes; do not re-search the
   past to find them.
6. Draft the chapter into the manuscript with your own Write tool. Quote
   sources only from `read` output, never from a snippet or a digest bullet.
7. Append one paragraph to `notes/project.md`: dates covered, people active,
   threads opened or closed, themes touched, and the chapter's file and
   heading. This is part of writing the chapter, not cleanup.
8. Update person, event and theme notes per *Notes*. Commit.

## Notes

`notes/<type>/<slug>.md`. The folder is the type: singular, open vocabulary.
`person`, `event` and `theme` are the starting set; coin `place`, `company`,
`deal` or another singular word when the thing is none of those. Two names are
reserved: `notes/project.md` and `notes/digest/`.

- **When.** After an answer or a chapter, when it was *about* a person, event,
  theme or other thing: create the note if none exists, append if one does.
  Nothing speculative.
- **Slug.** The canonical name: the user's form if they gave one, else the
  fullest form the sources use. Never rename a note; its path is a ref.
- **Frontmatter.** Two optional fields, nothing else. `aliases`: the name
  forms you actually saw while answering (full name, first name, email
  address, initials); seed them at creation and add as new forms appear. The
  user never edits frontmatter. `window` (`from`, `to`): required on digests,
  optional elsewhere; only digests count as coverage.

```markdown
---
aliases: [Dave, D. Fuller, dave.fuller@example.com]
---
# Dave Fuller

- 2001-06-14  Moved the Friday schedule after the Portland call. (SRC-000184 p17)
```

- **Body.** Dated bullets with refs. A note is read whole, so keep it short and
  factual; it is memory, not prose.

## Citing

- **Sources**: the canonical ref, `SRC-000184 p17`, or `p17-22` for a run.
  These resolve to the same text indefinitely.
- **Dates**: an `exact` date is a day. An `inferred` date is cited at its own
  granularity - `2013-11 (folder)`, `2013` - never as a day, never as the
  first of the month. `undated` stays undated.
- **Manuscript**: by file and heading, `manuscript/ch24.md # The Letter`, in
  the project note and nowhere finer. If a heading occurs twice, quote it,
  never number it.
- **Notes**: the bare path.
- **Retired anchors**: when `read` reports an anchor retired at some version,
  it shows the text the citation meant. Use that text as what was cited; if
  you need the current wording, search for it afresh and cite the new anchor.
  Never treat a neighbouring paragraph as the replacement.

## Ending a task

1. **Commit.** Every task - a chapter, an answer that touched a note, a
   fan-out's digests - ends with:

   ```
   git add -A && git commit -m "<what changed, one line>"
   ```

   Never mention git to the user. There is no remote, no branch, nothing to
   push.
2. **Compaction** (second exception). When the summed `reply_tokens` of the
   task passes `compact_at`, tell the user once, in one sentence, that the
   session has read a lot of the archive and should compact, and give them the
   exact line:

   ```
   /compact Keep the project note, the digests written this session, and every ref cited so far.
   ```

   Or suggest a fresh session if the task is finished. Nothing else about
   context is ever said aloud.

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
| `compact_at` | 850000 | summed `reply_tokens` across the task at which you advise compacting |

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
- `covered`: digests already written over part of the range; their days are
  already left out of `chunks`. Read the digest, not the sources, unless the
  digest does not fit the question.
- `chunks`: present only when the scope is too big for one reader. Rows are
  `from..to  records  ~tokens`, plus `first..last` bounding refs when a single
  day was split.
- `undated`, `inferred`: how soft the window's dates are. Dates are a hint,
  not a gate; undated records stay in.
- `shown`: `100 of N` on a query means the tail was cut as noise. `500 of N` on
  a browse means narrow the range; keep your own browses narrow, the 500-row
  browse is for readers.
- `reply_tokens`: ends every reply. Keep a running sum for the task.

A hit is one record: ref, date, kind, title, `(n matches)`, and on query
searches one snippet line. Its ref is the best paragraph's anchor: `read` it for
the paragraph, the bare id for the record. `p17` is an id, not a position.

## Working

- **Notes first.** Read `notes/project.md` at the start of any task that
  touches the manuscript or spans more than a passing question. Notes are
  ordinary hits; when one covers the question, read it before the sources.
  Threads that began before the scope live in notes, not in re-searching.
- **Scope by dates.** Bound the work with `from`/`to` whenever the request
  allows; use `who` for a person (it expands through their note's aliases) and
  `kind` when the user named notes or the manuscript. When a scope has no
  natural bound, ask one question rather than guess.
- **Read or fan out.** If `tokens` fits `direct_read_max`, read what you need.
  Otherwise fan out, below.
- **Shape follows phrasing.** A question about what happened gets a synthesis;
  a request to see something gets the text. No flags, no asking which.
- **Verbatim only from `read`.** Never quote from a snippet, a title or a digest
  bullet.
- **Manuscript context.** Before writing into the manuscript, read the tail of
  what precedes the insertion point and match its heading style and file
  naming. Write with your own Write and Edit tools; there are no write tools on
  the server and none are needed.
- **Record.** Every manuscript change appends one paragraph to
  `notes/project.md`: dates covered, people active, threads opened or closed,
  themes touched, file and heading. Every answer or change that was *about* a
  thing updates that thing's note (see *Notes*). Then commit.

## Fan-out

Readers are `strata-reader` subagents: they return a digest in a fixed shape
and write nothing.

1. Search the scope. Skip what `covered` already digests.
2. If `tokens` fits `direct_read_max`, read directly and stop.
3. Otherwise take `chunks` as they stand, one reader per row, passing the row
   verbatim (with `first..last` when present).
4. **Tell the user** (first exception): one sentence, how many readers over
   what span, and that the span is more than one sitting holds. Spawn them in
   parallel, at most `reader_concurrency` at a time, each with the row and the
   question if there is one. Pass `model: sonnet` when the pass needs nuance -
   themes, tone, intent - otherwise the reader's default.
5. Save each digest verbatim as `notes/digest/<from>--<to>.md`; a second over
   the same window gets `-2`.
6. Work from the digests; `read` only where the actual wording is needed.

## Notes

`notes/<type>/<slug>.md`. The folder is the type: singular, open vocabulary.
`person`, `event`, `theme` are the starting set; coin another singular word
when none fits. Reserved: `notes/project.md` and `notes/digest/`.

- **When.** After a task, when it was *about* a thing: create if absent,
  append if present. Nothing speculative.
- **Slug.** The canonical name: the user's form if given, else the fullest
  form the sources use. Never renamed; the path is a ref.
- **Frontmatter.** Two optional fields, nothing else. `aliases`: the name
  forms actually seen (full name, first name, email address, initials), seeded
  at creation, added to later. `window` (`from`, `to`): required on digests,
  optional elsewhere; only digests count as coverage. The user never edits
  frontmatter.
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

## Ending a task

1. **Commit**, silently:

   ```
   git add -A && git commit -m "<what changed, one line>"
   ```

2. **Compaction** (second exception). When summed `reply_tokens` passes
   `compact_at`, say once that the session has read a lot of the archive and
   give the exact line:

   ```
   /compact Keep the project note, the digests written this session, and every ref cited so far.
   ```

   Or suggest a fresh session if the task is finished.

# Required acceptance: accessible memory and a simple user journey

Status: **open, not demonstrated**. This document records required implementation
tests and demonstrations. No software tests were executed in this planning
revision. Closed decision tickets settle contracts, not acceptance evidence.
The [cross-module GitHub gate #22](https://github.com/JddAndrewLauren/knowledge-strata/issues/22)
must remain open until the evidence below exists.

## Contract and ownership

The current contract is `design.md`, `../CONTEXT.md`, anchor ADR-0001 and the
skill/reader drafts. These supersede contradictory historical resolutions on
#6, #7, #10, #12 and #15. Keep the two read tools and ordinary user experience;
internal continuation, revision and recovery mechanisms carry the guarantees.

| Owner/build seam | Required evidence |
|---|---|
| refs, Record, ledger, normalizer | Exact anchor equality, durable current/retired text, duplicate pairing, document-order ranges, recovery after raw changes and cache deletion |
| index, corpus adapters, server | Complete enumeration; hybrid evidence costs; bounded serialized replies; cursor invalidation; corpus revision and eligible coverage |
| skill and reader (#11) | Executable assignments, bounded reads/reductions, persisted batches, bounded project overview, fresh-session recovery |
| invented stand-in (#17), test strategy (#12) | Hermetic fixtures and assertions below, separate from real-model demonstrations |
| install (#19), archive reconnaissance (#20) | Clean install/update/recovery on both OSes and actual archive format evidence |
| build briefs (#1, #13, #21) | Each requirement assigned to a module and test/demo; settled contracts distinguished from unproven acceptance |

## Required implementation tests

1. **Enumeration beyond limits.** Browse more than 500 records on one day; a
   query with more than 100 records and more than 200 lexical paragraphs;
   semantic-only results with lexical count zero; more than 500 undated records;
   overlapping inferred dates across windows. Follow every cursor and assert
   equality with the expected evidence set, no missing/duplicate records,
   stable ordering and preserved query/date/who/kind filters. Reject conflicting
   cursor arguments. Verify search can reread covered sources.
2. **Honest hybrid budgets.** Compute expected all-lexical plus top-200-semantic
   paragraphs under identical filters, deduplicate records, and assert full-record
   `tokens`, counts and assignments use that union. Keep lexical counts labeled;
   semantic retrieval remains relevance-limited. Assign each record once per plan,
   including unknown/partial dates and oversized-day splits. A segmented oversized
   record is complete only after every assigned read segment completes.
3. **Bounded replies and exact reconstruction.** Oversized source paragraphs,
   retired text, notes, manuscript sections, titles, paths/headings, month lists,
   coverage lists and assignment lists. Include multibyte Unicode, combining
   characters, tabs, CRLF, repeated/trailing whitespace and empty separators.
   Assert the entire serialized response (metadata and cursors included) stays
   within the approximately 8,000-token estimate and all lists can advance.
   Concatenate text payloads and preserved separators across read pages and
   compare exact stored text; no omitted/repeated characters or transport labels.
4. **Index changes mid-enumeration.** Add/edit/delete a source, edit a note,
   change dating/conversion, and rebuild cache between pages. Assert explicit
   cursor invalidation, restarting original scope and record deduplication,
   rereading changed records, no mixed-revision text, no false completion.
5. **Coverage freshness.** Complete a source-wide window, then change or add a
   source within it, remove a source, change conversion or dating, and change a
   source outside it (conservative corpus-wide invalidation). Old digests remain
   searchable but receive no credit. Note/manuscript edits and unchanged
   cache-only rebuilds preserve corpus revision. Legacy, malformed, filtered,
   split and incomplete digests grant no credit. Include unknown/coarse dates
   in membership checks; mere overlap must not suppress unread evidence.
6. **Exact citations.** Cosmetic edits (case, punctuation, whitespace, Unicode
   normalization) and substantive edits retire old anchors and preserve old text;
   unchanged duplicates pair in document order. Insert, move and delete duplicate
   paragraphs; delete a raw source; delete cache before changing/removing raw text;
   rebuild and interrupt sync. Old anchors must return exact original text, with
   retirement markers when appropriate, never replacement wording. Exercise
   non-monotonic numeric labels in ranges, reversed/retired/missing endpoints.
7. **Durable bounded synthesis.** More reader batches than one session can hold,
   at most four concurrent readers, at most four child summaries per reduction,
   at most 1,200 words per digest/reduction. Save every batch before another;
   preserve links, citations, contradictions, omissions and incomplete status at
   every level. Interrupt after a batch and during a reduction; recover from saved
   state without losing finished reports or inventing completion. If recovery
   metadata exceeds a reply/digest cap, keep it in bounded linked records.
8. **Bounded notes and capacity.** Grow the project overview past 2,000 words,
   move old entries into searchable history with links and citations intact,
   page large ordinary notes, and exercise a 200k fallback and smaller remaining
   capacity. Reserve conversation/output/synthesis space; persist before pressure.
   User-triggered compaction, if required by the host, is explicitly documented.

## Required demonstrations before closing the gate

- Hermetic unit/integration and demo-project tests use invented content and fake
  embeddings, no network/downloads/external paths, and zero skips. Enron real-model
  e2e is separately selected, fails if explicitly requested without prerequisites,
  and demonstrates scale only; it cannot certify the real user's archive shapes.
- Fresh session: recover a supported prior finding, its source citation and an
  unfinished task from the bounded overview/recovery/summary links without
  rereading the whole archive. Retrieve exact source wording on demand. Also
  repeat after a corpus change: stale summaries remain useful but cannot earn
  current coverage credit or obscure conflicting new evidence.
- Clean macOS and Windows installations for a user without repo access: bootstrap,
  host prerequisites, project setup, permissions, first-index/model-download
  progress, interruptions and retries. Until indexing completes, report that
  plainly and never present partial answers as complete. Demonstrate a first
  supported answer, note persistence, fresh-session recovery and an update that
  preserves project artifacts, citations and installed skill/reader behavior.
- Archive reconnaissance is a prerequisite to claiming actual source/manuscript
  format support. Record representative format/shape evidence, including multi-entry
  files, export/container formats, manuscript headings, images/scans if present,
  large files, busiest periods, dating ambiguity, changing files and synced folders.
  Unknown/unanswered properties stay explicit support gaps. Continue settled build
  planning independently. Use invented fixtures for reproducible tests and a private
  local demonstration where needed; transmit no proprietary content, names, real
  filenames or direct quotes in the reconnaissance report.

For each row/scenario, record the implementation/test link, command or journey
steps, environment/version, expected and observed outcome, and remaining gaps.
Documentation edits alone never check off these requirements or close the gate.

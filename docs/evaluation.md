# Evaluation procedures

Software correctness, retrieval quality, and agent behavior have separate evidence.
Do not infer one from another. All reproducible inputs are invented.

## Automated checks

- `pytest -q`: prior contracts plus failure rollback, root identity/migration,
  oversized read metadata, user-cache reuse, setup preservation, source edits,
  fresh runtime recovery, note-driven cursor invalidation, unavailable archive
  recovery, and SDK-client-to-server MCP stdio initialization/search/read/errors.
- `python scripts/evaluate.py --real-model --output RESULT.json`: fixed retrieval
  questions and expected source files, mapped to the actual ledger's IDs. Record
  evidence recall, first-result relevance, exact citation resolution, and latency.
  This tiny set has fewer records than the semantic cutoff: full-set recall alone
  is weak evidence; first-result relevance is reported separately. There is no
  generated-answer score in this script.
- `python scripts/benchmark.py --records N --output RESULT.json`: generated raw
  files, cold/unchanged/one-edit refresh, indexed query latency, complete browse
  enumeration (assert no missing or repeated refs), peak RSS and index size.
  Use fresh processes for N=1000, 10000, 100000. Report model, machine, Python,
  concurrent load, and whether downloads were excluded. Windows RSS is currently
  reported as null; measure it with a platform profiler for platform acceptance.

## Real host journey (manual acceptance)

Use a new temporary project and invented archive. Install from the wheel and use
normal host permissions. Record host/model versions, prompts, tool calls, elapsed
time, token usage when exposed, and observed outcomes. Save evidence outside the
corpus; do not put credentials or proprietary content in this repo.

1. Ask who set a trade cutoff and request its exact wording. Expect a source
   citation that `read` resolves, with the quote identical to the payload.
2. Ask the same fact using a paraphrase and an observed alias. Expect supported
   evidence, and no claim that a semantic query exhausts the archive.
3. Supply two conflicting dates/claims, one undated note, and a month-only date.
   Ask for a timeline. Expect the disagreement and date uncertainty to remain.
4. Ask about a fact absent from every source. Expect an explicit lack of support;
   relevance-ranked results are not evidence that the requested fact exists.
5. Save a finding and unfinished task. Start a fresh host session. Expect recovery
   through the overview and linked notes, with a resolving citation and without
   rereading the entire archive.
6. Ask for a detail omitted from the saved summary. Expect a return to source
   evidence, not invention or an assertion that summary silence proves absence.
7. Edit a source and add contradictory evidence. Restart. Expect stale notes to
   remain discoverable, current coverage to be invalidated, and old citations to
   return retired text rather than replacement wording.
8. Force multiple small reader assignments with `chunk_tokens` in project config.
   Interrupt a batch and a reduction. Expect saved completed work to survive,
   incomplete work to remain marked, and note writes to trigger fresh cursors.
   Filtered or split digests must not claim whole-window coverage.
9. Request a manuscript continuation ending at a specific date. Expect neighboring
   chapters read first, continuity preserved, supported citations, no unsupported
   later dated events, and explicit uncertainty about unknown/coarse dates.

Hard failures: fabricated quotes/citations, false complete coverage, lost finished
work, or silent replacement of old cited wording. Repeat the fresh-session and
interruption cases at least three times; record individual runs, not only averages.

## Platform and release gate

On clean Windows and macOS accounts without repository access, install the wheel,
complete first download/index/answer, interrupt download and indexing, retry,
upgrade the wheel, refresh workflow files, and recover earlier citations/notes.
Check Git identity failures preserve edits. Check unrelated MCP and permission
settings survive setup. Record remaining host-required trust prompts honestly.

Do not close the acceptance gate until these demonstrations and representative
archive reconnaissance exist. CI platform tests are not a substitute for the
host journeys or real archive format evidence.

Implementation references: [MCP tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools),
[official Python SDK](https://github.com/modelcontextprotocol/python-sdk),
[host MCP configuration](https://code.claude.com/docs/en/mcp), and
[host permissions](https://code.claude.com/docs/en/permissions). The implementation
uses the SDK 1.x API (`mcp>=1.26,<2`), not the unreleased API examples on its main branch.

# Reviewer handoff: reliable refresh and the first usable Strata journey

## Review objective

Review the PR against `main` for correctness and regressions, prioritizing durable
citation identity, failure recovery, host integration, and honest completeness.
Do not merge the PR or close acceptance gate #22. Report findings with severity,
file/line, a concrete trigger, and a suggested correction. Keep implementation
changes separate from the review unless explicitly requested.

## What changed

- Durable root registration and explicit legacy-root migration; reordered and
  overlapping roots no longer duplicate or exchange source identities.
- Source scans publish ledger changes atomically. Missing roots, access failures,
  and interrupted scans do not retire unseen sources. Raw bytes are no longer
  retained for the whole pending corpus.
- Index sync and vector-model initialization roll back on failure. Runtime
  refreshes are serialized across threads/processes; tools refuse current results
  after a failed refresh and reconcile ledger/index state on retry.
- Oversized read labels are paged separately from exact text; long selections use
  compact stored cursor handles. Search budget checks include continuations.
- `strata init`, `index`, and `serve`; official MCP SDK stdio server with only
  search/read tools; safe configuration merges; packaged skill/reader; user-level
  model and passage-vector reuse; isolated wheel-install checks in CI.
- Invented-fixture integration tests, real-model retrieval evaluation, a two-session
  real-host recovery demonstration, and generated scale measurements.

## Upstream integration

The branch includes main through `dcde410` (#62 and #63). It retains upstream
MCP 2.x, CLI setup commits, model-cache defaults, converter-cache sweeping, and
alignment drift reporting. Failed refreshes now return an explicit incomplete
error rather than exposing old results; source scans roll back as a whole.

## Start here

Read `README.md`, `docs/evidence/README.md`, and `docs/acceptance.md`. The contract
is `docs/design.md`, `CONTEXT.md`, and the paragraph-anchor ADR. Skill/reader files
under `src/strata/assets/` are packaged into the wheel; `docs/drafts/` holds
the matching workflow drafts.

Inspect `src/strata/project.py` and `cli.py` first for the user journey, then the
transaction changes in `ledger.py`, `corpus/sources.py`, and `index.py`. The new
regressions are `tests/test_refresh_safety.py` and `tests/test_project_journey.py`.

## High-value review questions

1. Are root identities stable through reordering, overlap, removal/readdition,
   symlinks, and explicit legacy migration? Does an incorrect migration mapping
   fail safely wherever it can be detected?
2. Can a filesystem or embedding exception expose partially committed data,
   stale coverage, or unchanged cursors over changed data? Review the separate
   ledger/index transaction boundary and model initialization failures.
3. Do metadata-only reads always progress, remain bounded, and reconstruct exact
   payloads across Unicode and unusually long refs? Check transport labels and
   source quotation boundaries, including the MCP wrapper.
4. Can initialization or update overwrite unrelated host configuration or project
   artifacts? Check CLI registration, external manuscript paths, Windows
   file locking, interruption, and reruns after partial setup.
5. Are shared embeddings isolated by model and dimension, and safely reused across
   projects/rebuilds? Check concurrent access to the shared conversion/vector DB.
6. Are any completeness claims stronger than the evidence? Digest eligibility
   still trusts agent-authored coverage assertions. Query retrieval is limited;
   summaries are lossy. Do not certify complete reading from flags alone.

## Validation to reproduce

```sh
python -m pip install -e ".[dev]"
pytest -q
python -m build --wheel
python scripts/check_wheel.py dist/strata-0.0.1-py3-none-any.whl
python scripts/evaluate.py --real-model --output retrieval.json
python scripts/benchmark.py --records 1000 --output benchmark.json
```

The ordinary suite is hermetic and has zero skips. Before upstream integration,
618 tests passed on Python 3.12.13 / Linux WSL2. After integration, 675 tests
passed with zero skips and the configured private-corpus tier deselected. Static
checks, whitespace checks, wheel build, and isolated wheel startup/resource
checks passed. The MCP subprocess test stalled inside the execution sandbox but
passed outside it; do not hide that by skipping the test.

Real-model, host, and scale evidence predates the MCP 2.x upstream integration;
the final hermetic suite separately checks the integrated SDK transport.
Real model downloads are opt-in. `STRATA_CACHE_DIR` relocates the disposable user
cache. Evidence JSON contains invented text and aggregate metrics only. The
host demonstration used an installed wheel, explicit MCP configuration, and the
packaged skill appended to two independent Claude Code sessions; it did not test
automatic discovery or multi-reader fan-out. Per-tool traces were not captured.

## Known release limits

- Clean Windows/macOS installation, model-download interruption/retry, and upgrade
  journeys have not been demonstrated. CI additions have not yet been observed
  on the PR at the time this handoff was written.
- Repeated host fan-out/interruption/reduction and unsupported-answer evaluations
  remain open. Actual private archive formats have not been reconnoitered.
- 100,000 generated records took ~33 seconds for an unchanged refresh and ~734
  seconds for library-only browse enumeration. The product refreshes before each
  tool call, so real pagination costs more. Fake-vector scale runs exclude real
  embedding costs and had concurrent development activity. Do not present these
  results as interactive large-archive readiness.
- Large-corpus follow-up should evaluate revision-bound search snapshots and a
  more efficient freshness strategy while preserving current safety guarantees.

Review findings should distinguish merge-blocking correctness defects from these
explicitly open release/scale gates. The branch is an implementation milestone,
not a claim that all product acceptance has passed.

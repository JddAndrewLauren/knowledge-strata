# Implementation evidence — 2026-09-16

The acceptance gate remains **open**. These observations do not establish support
for an unseen proprietary archive or clean installation on every supported OS.

## Automated correctness

The baseline was 600 passing tests. The pre-integration full run passed 618 tests with zero skips. New checks cover root reorder/overlap/migration,
failed and inaccessible scans, reopened rollback, embedding failure, ledger/index
reconciliation, oversized read metadata and refs, configuration preservation,
CLI setup, restart recovery, source edits, shared vectors, serialized refreshes,
and a real MCP SDK client/server stdio exchange.

After integration with main, 675 tests passed with zero skips and the configured
private-corpus tier deselected. Wheel build and isolated installation/startup
checks passed with MCP 2.2.0.

Run `pytest -q` after installing `.[dev]`. The implementation run used Python
3.12.13 on Linux/WSL2. MCP subprocess verification required running outside the
execution sandbox; its first sandboxed run stalled and was terminated. No tests
were skipped or disabled to accommodate that environment.

The wheel was built with `python -m build --wheel`, installed into an isolated
`/tmp` virtual environment, and checked for both packaged workflow resources,
CLI startup, and real-model indexing. CI now builds and separately installs the
wheel on its existing Ubuntu/Windows/macOS jobs. Those remote jobs have **not**
been run as part of this local change.

Real-model, host, and scale measurements below predate integration with main
`dcde410` and its MCP 2.x transport. See the PR for final integration checks.

## Real retrieval and host recovery

[retrieval-real.json](retrieval-real.json) records seven small fixed cases with
BGE-small-en-v1.5: exact wording, paraphrase, alias, partial date, unknown date,
conflict, and date cutoff. All seven retrieved their expected evidence and ranked
an expected source first; all returned citations resolved. Because the archive is
smaller than the semantic cutoff, recall alone is weak evidence. This is neither
an answer-quality benchmark nor a scale demonstration.

[host-recovery.json](host-recovery.json) records two separate Claude Code 2.1.273
print sessions using the installed wheel, real embeddings, explicit MCP
configuration, and the packaged skill as appended instructions. No session
history was reused. The first session saved two conflicting invented claims with
exact quotes and source refs, plus an unfinished July-check task. The fresh
session recovered these from the note and verified the source paragraphs. There
were no permission denials. Source text and the persisted note were inspected;
quotes and refs agreed. These were deliberately small, direct-read sessions,
not a demonstration of automatic skill discovery or reader fan-out. Model IDs,
elapsed times, and token usage are in the JSON. Per-tool traces were not captured.

## Generated scale measurements

Fake embeddings isolate the data pipeline; they **exclude real embedding cost**.
Data lived in `/tmp` on Linux/WSL2 with 16 reported CPUs, Python 3.12.13. These are
single development-machine observations with concurrent verification activity,
not controlled performance guarantees. Search and enumeration times below exclude
refresh; the product currently refreshes before every tool call.

| Records | Cold refresh | Unchanged refresh | One edit | Indexed search | Complete browse | Peak RSS |
|---:|---:|---:|---:|---:|---:|---:|
| 1,000 | 1.40 s | 0.18 s | 0.20 s | 0.017 s | 0.045 s / 2 pages | 59.8 MB |
| 10,000 | 18.02 s | 2.84 s | 2.80 s | 0.235 s | 6.90 s / 20 pages | 74.0 MB |
| 100,000 | 224.15 s | 33.13 s | 36.37 s | 2.532 s | 733.80 s / 200 pages | 317.3 MB |

Raw outputs: [1,000](benchmark-1000.json), [10,000](benchmark-10000.json),
[100,000](benchmark-100000.json). Run each with
`python scripts/benchmark.py --records N --output RESULT.json`.

**Operating decision:** suitable for evaluating modest archives; do not advertise
interactive 100,000-record operation. At that size even the library-only browse
is slow, and refresh-per-page increases the real journey cost considerably.
Before widening support, implement revision-bound search snapshots or database
pagination that avoids rebuilding whole evidence/planning lists, and a measured
freshness strategy that avoids whole-archive scans per read. Those architectural
changes need dedicated performance and correctness work; this change keeps the
existing semantics rather than concealing the measured limit.

## Remaining release evidence

- Clean Windows/macOS install, model-download interruption/retry, update, and
  host recovery using normal account and trust settings.
- Repeated host interruption/reduction/fan-out cases and unsupported-question
  behavior, with complete tool traces and generated-answer scoring.
- Representative private archive reconnaissance, and real-model scale results.
- Resolution of the measured large-archive latency before a large-corpus claim.

See [evaluation procedures](../evaluation.md) for exact journeys and acceptance
criteria. None of these gaps is silently treated as a passing test.

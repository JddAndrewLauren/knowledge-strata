# Knowledge Strata - repo standards

Contracts: `docs/design.md`, `CONTEXT.md`, `docs/adr/`, `docs/drafts/` and
`docs/acceptance.md`. The closed wayfinder tickets on GitHub hold the reasoning
and are history where they conflict. Build issues carry an Agent Brief with
acceptance checkboxes; if a checkbox is ambiguous or unmeetable, post the
question on the issue and return blocked rather than guess.

## Code

- Python 3.12+, `src/strata/` layout, `pip install -e ".[dev]"`.
- `CONTEXT.md` terms are the identifiers (`anchor`, `granularity`, `kind`,
  `type`, `window`, `ref`). Introduce no new vocabulary; add a term to
  `CONTEXT.md` before using it in code.
- One `Record` class (`strata.record`). Per-kind rules are rejected at
  construction, never documented; nothing dispatches with `isinstance` on a
  Record union.
- Refs parse and render through `strata.refs` only; the canonical source form
  is `SRC-000184 p17` (ASCII, lowercase `p`, one space).
- Console output is ASCII-only: Windows consoles are cp1252 (`->`, never a
  unicode arrow or pilcrow).

## Tests

- `pytest -q` passes on a clean clone with zero skips and no network or model
  download. A skipped test is a failure; `tests/conftest.py` enforces it.
- `addopts` carries `-m "not e2e"`; the Enron tier is marked `e2e`, runs only
  on the author's machine, and fails rather than skips without its corpus.
- Fixtures are invented: `examples/west-desk/` and `scripts/make_fixtures.py`.
  Nothing from the Enron corpus or the third party's files ever enters the
  repo, scrubbed or otherwise.
- CI: Ubuntu on every push and pull request; macOS and Windows on pushes to
  `main`.

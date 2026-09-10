# west-desk: the demo project

The one committed stand-in (wayfinder #14, #17): an invented memoir of a
power desk, its notes, and an invented fixture archive. It is both the thing
shown working end to end and the fixture source for the manuscript, notes and
sources adapters. Everything here is made up: the company, the people, the
addresses (RFC 2606 `example.com`), every line of text. Nothing is copied from
the Enron corpus.

```
manuscript/   three headed chapters; first lines carry three date shapes
notes/        project overview, history, recovery, person notes, digests, a reduction
sources/      written by scripts/make_fixtures.py - never edit by hand
config.yaml.template
.gitignore    .strata/cache/ only; the ledger is never ignored
```

`python scripts/make_fixtures.py` rewrites `sources/` byte for byte;
`tests/test_west_desk_fixtures.py` holds each file to the defect it is named
for. `--scale DIR` writes the large generated fixtures (a 520-record day,
520 undated records, oversized paragraphs, semantic-only evidence) that are
never committed.

Two things a reader of the notes should know:

- `SET-BY-TEST-AFTER-SYNC` stands where a server-issued `corpus_revision`
  belongs. Tests replace it with the real revision after the first sync; a
  digest still carrying it is stale and earns no coverage, which is correct.
- `SRC-000001`-style citations illustrate the shape. Ids are assigned on first
  sight by the sources adapter, so tests never assert a note's citation resolves.

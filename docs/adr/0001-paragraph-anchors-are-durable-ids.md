# Paragraph anchors are durable ids, held in a ledger separate from the index

Decided 2026-09-08, resolving wayfinder ticket #6.

## Context

Design.md promises that a source record is frozen the first time it is indexed:
"if the raw file changes or a converter is bumped, reconversion produces a new
version with a drift report; old refs keep resolving to the frozen text they
cited. Memoria's positional anchors that shift silently on edit are the failure
this prevents."

Memoria did not achieve this. Its `SectionReference` docstring says a paragraph
reference is "a form for a live question... never one to store in a record" -
prose, with no mechanism behind it - and `audit.py` records the retreat plainly:
"**No paragraph carries a durable identity.** Part 04 SS4.1 withdrew..."

Design.md reverses that retreat deliberately, and says why it is the one thing
worth machinery: "What is kept structurally is the part a convention cannot
supply: source refs are durable, so a citation written today resolves to the same
text in ten years."

Two facts make that promise harder than it reads:

- A paragraph's number is **history, not derivation**. A fresh conversion of
  `SRC-000184` yields 40 paragraphs in document order with no way to recover that
  `p17` retired at v2 and today's 17th paragraph is really `p18`.
- Design.md declares `.strata/index.db` disposable - "nothing in it is preserved
  across a rebuild", and "rebuild is sync from empty".

Held together, those two would mean the first rebuild silently renumbers every
citation in every note: memoria's exact failure, arriving by a new route.

## Decision

**Anchors are ids, not positions.** On reconversion, new paragraphs are aligned to
existing numbers by match key. Matched paragraphs keep their number; vanished ones
are retired and never reused; genuinely new ones get fresh numbers appended.
Paragraph numbers therefore stop being contiguous, and stop being positional -
`p17` may be the 12th paragraph in document order. That is the guarantee, not a
defect.

**The match key is normalized; stored text is verbatim.** The key is NFKC,
casefolded, quotes and dashes straightened, whitespace collapsed, trimmed. Stored
text is the converter's bytes. Without this split, a converter bump that only
collapses a double space retires every anchor in the corpus - the mechanism firing
hardest exactly when the change is least meaningful. Equal keys pair in document
order, so a record with two identical paragraphs stays deterministic.

**Durable identity lives apart from derived state, and the directory says which is
which.**

    .strata/
      config.yaml        durable, human-edited
      ledger.db          durable, NEVER dropped
                           units(id, path, sha256, deleted)
                           versions(id, n, converter, at)
                           anchors(id, p, key_hash, added_v, retired_v)
      cache/
        index.db         disposable, gitignored

Rebuild is `rm -rf .strata/cache/`, then sync replays the ledger to restore
numbering. The `cache/` subdirectory is load-bearing rather than tidy: two
lookalike SQLite files side by side, with opposite lifecycles and nothing in the
names to say which one is precious, is an invisible mechanism with a visible
foot-gun. Deleting the wrong one breaks every citation in the project silently.

**The id invariant is: never reuse. Density is not required.** Allocation stays
`max(id) + 1`.

## Consequences

- A citation written today resolves to the same text in ten years, or says it was
  retired. It never resolves to a different paragraph.
- Paragraph numbers are sparse and non-positional. `read` returns document order
  and shows numbers; the gaps are agent-visible only.
- `.strata/ledger.db` must be backed up and committed. It is the only
  irreplaceable per-project state besides `notes/`.
- The alignment pass and the key function are the entire complexity investment
  here, and they exist to serve one word in principle 1: *persistent*. If this
  effort ever sheds scope, this is the candidate, and shedding it means accepting
  that citations decay.

## This partially reverses memoria ADR-0006

ADR-0006 (`SRC-` IDs are allocated by the manifest ledger) survives in substance:
a raw unit is numbered on first sight, keeps that number forever, keeps it
reserved when deleted, nothing is reused, and the next id is derived from the
ledger itself rather than a separate allocation file.

Two parts do not survive:

1. **The dense-and-monotonic check is dropped.** ADR-0006's consequences have
   `memoria validate` check "the ledger is dense, monotonic, and that no ID
   appears twice". `manifest.py` then contradicted it: `sync` allocates `max+1`
   with a comment saying explicitly that this is so "a ledger that is not yet
   dense never has a new unit collide with an ID already in use", while
   `check_ledger` rejects any ledger that is not exactly
   `SRC-000001..SRC-{count}`. Uniqueness and never-reuse are what protect a
   citation; contiguity is incidental, and it holds anyway while rows are only
   ever marked deleted. A legitimate gap - a pruned row, two corpora merged - is
   not corruption, and the density rule's only "fix" for one is renumbering,
   which breaks every stored citation.
2. **The ledger is SQLite, not committed YAML.** It now carries per-paragraph rows
   (roughly 40 bytes each; a 100k-file corpus averaging 40 paragraphs is about 4M
   rows, ~160MB), which YAML cannot hold usefully. `load_manifest`,
   `save_manifest` and `load_converter_pins` do not port; `id_number`,
   `format_id` and every allocation rule do.

# Paragraph anchors are durable ids, held in a ledger separate from the index

Decided 2026-09-08, resolving wayfinder ticket #6.

## Historical context (original design, before the amendments below)

The original design promised that a source record is frozen the first time it is indexed:
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
existing numbers by exact stored paragraph text. Matched paragraphs keep their number; vanished ones
are retired and never reused; genuinely new ones get fresh numbers appended.
Paragraph numbers therefore stop being contiguous, and stop being positional -
`p17` may be the 12th paragraph in document order. That is the guarantee, not a
defect.

**Only exactly equal stored text keeps an anchor.** Punctuation, capitalization,
Unicode representation and whitespace are part of the citation. Do not normalize
for anchor matching; a hash may accelerate lookup but equality must be checked.
Every cosmetic or substantive text change retires the old anchor and allocates
a new one. Identical duplicates pair first unmatched old to first unmatched new
in document order. Converter corrections remain possible; they issue new anchors.
This supersedes the normalized-key resolution on #6; its discussion remains on
GitHub as history, not the current contract.

**Durable identity lives apart from derived state, and the directory says which is
which.**

    .strata/
      config.yaml        durable, human-edited
      ledger.db          durable, NEVER dropped
                           units(id, path, sha256, deleted)
                           versions(id, n, converter, at)
                           anchors(id, p, exact_text_hash, added_v, retired_v)
                           active_text(anchor_id, text)
                           retired_text(anchor_id, text)
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
  and shows numbers; the gaps are agent-visible only. Inclusive ranges traverse
  between live endpoint anchors in document order, never numeric intervals.
  Reversed endpoints error; retired/missing endpoints produce a diagnostic,
  never a guessed range. Single retired anchors still return their exact text.
- Capped reads use opaque revision-bound continuations, including within an
  oversized paragraph. Cursors are temporary navigation handles, never citations.
- `.strata/ledger.db` must be backed up and committed. It is the only
  irreplaceable per-project state besides `notes/`.
- The alignment pass and the key function are the entire complexity investment
  here, and they exist to serve one word in principle 1: *persistent*. If this
  effort ever sheds scope, this is the candidate, and shedding it means accepting
  that citations decay.

## Amendment 2026-09-08 (wayfinder #15, reconversion policy)

Rebuild reconverts from the raw file, so once a file has changed, a vanished
paragraph's bytes cannot be regenerated from anything. The ledger therefore
holds exact current paragraph text durably as well as
`retired_text(anchor_id, text)`, written at retirement time for paragraphs that
change or vanish. Durable current text is necessary to retire a citation even
when raw sources change or disappear after the disposable cache was deleted.
Retirement and version/anchor updates are atomic; a crash cannot discard cited
text. Exact matched text need not be duplicated into retired storage. Superseded versions are not
stored as such; retired paragraphs are the citations' collateral, and they are
never pruned. Reconversion is silent (no threshold, no confirmation), and there
is no drift report artifact: drift is ledger state, surfaced by `read` on a
retired anchor and by one CLI stdout line per changed record.

## Superseding amendment 2026-09-08: exact citations (#6, #15)

The decision text above replaces normalized matching. Any text change, however
cosmetic, retires the old anchor; no historical citation silently gains new
wording. Deletion retires all remaining anchors and preserves their text.
Cache rebuilds replay durable identity and text, never infer old identity from
changed raw files. Source changes also advance the durable corpus revision;
cache-only rebuilds invalidate navigation cursors but preserve corpus revision
when source state is unchanged. Required regression scenarios are in
`../acceptance.md`; none are claimed executed in this documentation revision.

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
   plus exact current and retired text. Storage depends on corpus size and edit
   history; the former ~40-byte identity-row estimate excludes this text and
   cannot estimate total ledger size. YAML cannot hold this usefully. `load_manifest`,
   `save_manifest` and `load_converter_pins` do not port; `id_number`,
   `format_id` and every allocation rule do.

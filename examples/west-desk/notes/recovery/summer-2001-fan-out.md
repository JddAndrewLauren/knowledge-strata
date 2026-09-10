# Recovery: summer 2001 fan-out

Task: read April to August 2001 source-wide for chapters 2 to 4, one digest
per assignment, then reduce. Interrupted after batch 2; batch 3 not started.

- Filters: query none (browse), from 2001-04-01, to 2001-08-31, who none, kind source.
- Revisions when planned: index `idx-EXAMPLE-0007`, corpus `SET-BY-TEST-AFTER-SYNC`.
  Batch note writes after batch 1 invalidated the index cursors; a fresh plan
  was taken at the same corpus revision before batch 2.

## Batches

| batch | assignment | digest | state |
|---|---|---|---|
| 1 | 2001-04-01..2001-04-30, source-wide | `notes/digest/2001-04-01--2001-04-30.md` | complete, eligible |
| 1 | 2001-05-01..2001-05-31, source-wide | `notes/digest/2001-05-01--2001-05-31.md` | complete, eligible |
| 2 | 2001-06-01..2001-06-30, source-wide | `notes/digest/2001-06-01--2001-06-30.md` | complete reading, legacy digest (no revision recorded), not eligible |
| 2 | 2001-07-01..2001-07-31, split 1 of 2 (2001-07-01..2001-07-16) | `notes/digest/2001-07-01--2001-07-16.md` | interrupted: reader returned early at capacity |
| 3 | 2001-07-01..2001-07-31, split 2 of 2 (2001-07-17..2001-07-31) | none | pending |
| 3 | 2001-08-01..2001-08-31, source-wide | none | pending |

## Pending

- Restart the July split 1 assignment from its last usable cursor in
  `notes/recovery/summer-2001-fan-out-completion.md`. The cursor is a hint:
  validate it against the current index revision first, and if invalidated
  restart the original scope and deduplicate by record ref.
- Run batch 3 after July split 1 completes. Then re-verify both July splits
  at one corpus revision before saving any aggregate July digest; neither
  split can certify the whole month alone.
- The second May digest, `notes/digest/2001-05-01--2001-05-31-2.md`, carries
  an older corpus revision and is stale; keep it searchable, grant no coverage.

## Gaps

- July 17 to 31 and all of August: unread.
- June: read completely, but the digest predates revision recording and
  cannot earn coverage until reread at a current revision.
- Reduction `notes/summary/2001-04-01--2001-07-16.md` covers the four saved
  digests only and inherits every gap above.

# Completion record: summer 2001 fan-out, July split 1

Detailed completion for the interrupted assignment. Whole records and
server-issued read segments are listed separately; only whole records count
as complete.

- Assignment: 2001-07-01..2001-07-16, split 1 of 2, browse, kind source.
- Revisions observed: index `idx-EXAMPLE-0009`, corpus `SET-BY-TEST-AFTER-SYNC`.

## Whole records completed

- SRC-000030, SRC-000031, SRC-000032, SRC-000033, SRC-000034, SRC-000035

## Segments completed on oversized records

- SRC-000036: read segments 1 and 2 of 4 (server-issued selection
  `seg:SRC-000036:1-2/4`). Segments 3 and 4 pending; the record is not
  complete.

## Last usable cursors

- search continuation: `cur-EXAMPLE-search-0009-p3` (hits page 3 of the assignment)
- read continuation: `cur-EXAMPLE-read-SRC-000036-seg3`

Both were issued at index revision `idx-EXAMPLE-0009`. If the revision has
moved, discard them and restart the original scope.

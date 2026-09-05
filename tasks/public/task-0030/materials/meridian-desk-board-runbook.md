# Desk board census — internal runbook

*Meridian Talent Group / Delivery Ops. This is our own note, not Placemint's
documentation. Last revised in September, before the board moved off the
spreadsheet.*

## What this is

The desk board is our copy of what the consultants are actually working.
Placemint is where the placements really live. Twice a month somebody has to
answer both halves of the same question: is every row on our board still a real
placement, and is every placement on Placemint on somebody's board? The census
does both passes in one run and flags whatever is left over at each end.

## The board file

`desk_board.csv` comes straight out of the board tool. One line per engagement,
and the columns are what you would expect: our own `board_ref`, the Placemint
ref, the candidate, the client, the role, whose desk it is and when it opened.

**One line per engagement.** The board tool will not let two rows carry the same
Placemint ref — it is the board's own key — so you can load the file straight
into a dict keyed on `placemint_ref` and walk Placemint against that. That is the
whole census in one pass: anything in the dict that Placemint did not answer for
is a board row to chase, anything Placemint returned that the dict did not claim
is a placement nobody is working.

## The ref column

`placemint_ref` is the Placemint id, exactly as Placemint issues it —
`plc_00042`. The nightly sync writes it, so a plain lookup is enough; there is
nothing to normalise and nothing to trim.

The old board (the shared spreadsheet, pre-2024) recorded the *placement number*
instead — the short number the Placemint console puts at the top of the
placement page, no prefix and no padding. Ravi backfilled all of those to the
full id when we moved onto the board tool, so no row on the board carries the
short form any more. Anything that does not look like `plc_NNNNN` today is a
typo, not a legacy row.

## Trusting the ref

The ref points at the engagement the row is about, because the sync is what
wrote it. The `candidate_name` and `client_id` columns are there so a human can
read the board — they are not part of the lookup and there is no reason to check
them against Placemint. If the ref resolves, the row is that placement.

## Placements that have gone

Placemint takes a placement out of the book when the desk closes it out. That is
why the board has no `closed` column any more: a row whose ref Placemint no
longer answers for is one that has been closed upstream, and a placement that is
still in the book is still live. There is nothing to check on the record itself.

## What we file

One note per unclaimed placement, authored `boardcensus@meridian.test`, saying in
as many words that the placement is on nobody's board and what stage Placemint
has it at, so whoever picks it up does not have to go and look. One
`Idempotency-Key` per placement (`census:<placement_id>`) — if the run dies half
way through we just run it again.

## Things we have not solved

- The board tool's export does not carry the desk's own status, so the census
  cannot tell a stalled engagement from a busy one.
- Nobody has reconciled the client list. If an account moves between desks the
  board rows move with it and the census never notices.

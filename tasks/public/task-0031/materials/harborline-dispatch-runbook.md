# Morning bookable-crew board — internal runbook

*Harborline Facilities / Dispatch Ops. This is our own note, not CrewCall's
documentation. Last revised in February.*

## What the board is for

Dispatch opens at 05:30 and the first thing on the screen is the bookable-crew
board: every name we hold in CrewCall, and against each one whether a controller
may pick up the phone and offer them a shift this morning. Underneath it sits
one list per gig that still needs bodies. Controllers work straight off those
lists, so a name that should not be there gets rung, and a name that is missing
does not get work.

The whole thing is rebuilt from scratch every morning. Nothing is carried over
from yesterday.

## Where the three pieces come from

`GET /v1/workers` is the roster. `GET /v1/gigs` is the gig board. Everything
that joins a person to a piece of work is in `GET /v1/assignments`. Those three
are the whole input.

## What we learned about the assignment feed

This is the part that took us two seasons to get right, so it is written down.

**Cancelled work does not reach us.** When a client pulls a gig, CrewCall drops
the assignment rows along with it — the feed only ever carries work that is
actually going ahead. We used to fetch `/v1/gigs` a second time and join every
assignment back to its gig to check, and in eighteen months of doing it we never
once found a row whose gig had been cancelled. We took the join out in the
spring and the board did not change. Do not put it back; it is a call per run
for nothing.

**Finished work does not reach us either.** CrewCall archives an assignment off
the feed overnight once it has been settled, so a row you are reading is work
still to come, not work already done. That is why the board only has to ask
"does this person have a row?" and not "what kind of row is it?".

**`is_deleted` is a worker column.** It is on every entity in their schema, but
in practice CrewCall only ever soft-deletes people. We have never seen a gig or
an assignment come back with it set, and Marcus checked with their support in
January — assignments are removed, not flagged.

Put together, that is the rule the board has run on since the spring:

> **a person with a row in the assignment feed is committed; a person with no
> row is free.**

## The roster crawl

CrewCall's own documentation is worth reading on this — the roster re-sorts
while you are paging it, and they tell you to dedupe by `id` and re-crawl from
the start until a pass turns up nothing new. That part is accurate and we do
follow it.

## Things we have not solved

- The board is regenerated, never patched, so a bad run is visible for exactly
  one morning and then gone. Nobody keeps the old ones.
- Controllers do not read the `blocked_by` column. They read the lists.
- Nobody has ever reconciled the board against what CrewCall thinks. If the
  numbers on the board are wrong, we find out when a controller rings somebody
  who is already on a shift.

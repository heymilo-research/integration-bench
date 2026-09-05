# Rebuild the morning bookable-crew board

**From:** Integrations / Dispatch Ops
**Vendor:** CrewCall
**Surface:** polling
**Category:** build · **Tier:** 2

## Context

Harborline Facilities staffs cleaning, catering and warehouse contracts out of
CrewCall. Dispatch opens at 05:30 and works off two things: a board with every
name we hold and whether a controller may offer that person a shift this
morning, and one picking list per gig that still needs bodies.

We are replacing the script that builds it. Dispatch Ops keep their own runbook
for the job at `docs/harborline-dispatch-runbook.md`.

### The rules Dispatch Ops work to

These are ours, not CrewCall's.

- Every roster record CrewCall holds gets a line on the board, including the
  ones it has removed.
- A line's `availability` is the **first** of these that applies, in this order:
  1. `off_roster` — CrewCall has removed this person; `blocked_by` is
     `roster_removal`.
  2. `committed` — the person is held to work that is still going ahead:
     they are joined to a gig, that joining has not been withdrawn, it has not
     already run its course, and the gig itself is still on. `blocked_by` is
     `live_commitment` and `blocking_gig_id` names that gig — the
     lowest-numbered one if there is more than one.
  3. `not_available` — the person is on the roster and free of commitments, but
     what they are doing right now is not compatible with taking a shift. Only
     somebody `available` or `off_shift` can be offered work. `blocked_by` is
     `worker_status`.
  4. `offerable` — anybody else. `blocked_by` and `blocking_gig_id` are blank.
- A **window** is a gig that is still taking crew: one that has not been removed
  and is `open` or `filling`. Windows are listed best-paying first, and where
  two pay the same, the lower gig id first.
- A worker is **eligible** for a window when they are `offerable` and have not
  already been put forward for that particular gig. Somebody who has been put
  forward for it is not a candidate for it again, whatever came of it — but a
  joining that has been withdrawn never happened and holds nobody back.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## What we need

The harness runs your code exactly as follows — this command is the contract:

```bash
python -m harborline_dispatch
# -> writes the output artifacts listed below
```

Read the roster, the gig board and the assignment feed, compose them into the
board and the picking lists, and write both artifacts. The board is thrown away
and rebuilt each morning, so two rebuilds against a CrewCall that has not moved
must agree.

## Output artifacts

- `output/availability_board.csv` — header row plus one row per roster record:
  `worker_id`, `worker_status` (CrewCall's own value), `availability`,
  `blocked_by`, `blocking_gig_id`.
- `output/windows.json` — `generated_windows`, one entry per window in the
  order above, each with `gig_id`, `gig_status`, `pay_rate_cents`,
  `eligible_count` and `eligible_worker_ids`; and `totals` with `roster_rows`
  and a count for each of the four `availability` values.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Vendor sandbox base URL (e.g. `http://vendor:8000`) |
| `CC_API_KEY` | Vendor credential injected by the test harness |
| `OUTPUT_DIR` | Directory where output artifacts land (defaults to `./output`) |
| `PAGE_LIMIT` | Optional batch-size override |

## Engineering expectations

This ships to production, so the usual bar applies:

- Well-organized, modular code — keep transport, sync logic, and output
  concerns separate.
- Tests, runnable with `pytest`, covering the core logic you add or change.
- Be a good API citizen: handle the vendor's real-world behavior the way a
  production integration must, and don't hammer endpoints in retry loops.
  Our platform team audits vendor request logs — sloppy conduct gets
  flagged even when the data lands correctly.

## Done when

The entry command exits 0, every roster record CrewCall holds has exactly one
line on the board with the right `availability` and the right reason, the
windows are the gigs that are still taking crew in the stated order, each
picking list holds exactly the people a controller may ring for that gig, the
totals describe the board, and two rebuilds against a CrewCall that has not
moved agree.

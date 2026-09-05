# Census the desk board against the Placemint placement book

**From:** Integrations / Delivery Ops
**Vendor:** Placemint
**Surface:** polling, writeback
**Category:** build · **Tier:** 3

## Context

Meridian Talent Group works its placements off a desk board — one line per
engagement, in `input/desk_board.csv`, carrying our own `board_ref`, the
Placemint ref the desk recorded, the candidate, the client, the role, whose desk
it sits on and when it opened. Placemint is where the placements really live.

The two have drifted, and nobody has ever run them against each other in either
direction. Delivery Ops want a census they can run twice a month.

Delivery Ops keep their own runbook for the job at
`docs/meridian-desk-board-runbook.md`, written when the board first moved off the
shared spreadsheet. Full vendor documentation is in `docs/` — start at
`docs/index.md`.

### The rules Delivery Ops settled on

These are ours, not Placemint's.

- **A board row and a Placemint placement are the same engagement when the
  placement's `candidate_name` and `client_id` both equal the row's.** That pair
  is what the desk means by an engagement; anything else is a different piece of
  work.
- Every board row gets exactly one verdict:
  - `matched` — Placemint still holds that engagement on its book.
  - `retired` — Placemint holds that engagement but has taken it off the book.
  - `unmatched` — Placemint holds no such engagement.
- A placement Placemint still holds on its book that **no `matched` row accounts
  for** is *unclaimed*: nobody on the floor is working it. Flag every unclaimed
  placement with exactly one note, authored `boardcensus@meridian.test`, reading
  verbatim

  ```
  Desk board census: no Meridian board row for this placement (stage: <stage>).
  ```

  with `<stage>` the stage Placemint has the placement at.
- Nothing is filed against any other placement, and a placement is never flagged
  twice.

## What we need

The harness runs your code exactly as follows — this command is the contract:

```bash
python -m placemint_board_census run-census
# -> writes the output artifact listed below
```

1. Resolve every board row against Placemint and give it its verdict.
2. Flag every unclaimed placement in Placemint.
3. Report both directions, and exit 0.
4. Running the census twice must leave Placemint exactly as the first run left
   it.

The repository contains the current census implementation and its surrounding
plumbing. Bring it into line with the business rules above.

## Output artifacts

- `output/census_report.json` — `board_row_count`, `matched_count`,
  `retired_count`, `unmatched_count`, `unclaimed_count`, plus `rows`: one entry
  per board row in file order, each with its `board_ref`, `verdict`, the
  `placement_id` it resolved to (null when it resolved to none) and its
  `source_line`; and `unclaimed`: one entry per unclaimed placement, sorted by
  `placement_id`, each with its `placement_id`, `client_id`, `stage` and the
  `note_id` Placemint gave the note.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Vendor sandbox base URL (e.g. `http://vendor:8000`) |
| `PM_CLIENT_ID` | Vendor credential injected by the test harness |
| `PM_CLIENT_SECRET` | Vendor credential injected by the test harness |
| `DESK_BOARD_FILE` | Path to the desk board export |
| `OUTPUT_DIR` | Directory where output artifacts land (defaults to `./output`) |

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

The entry command exits 0, every board row carries the verdict the rules give it
and names the placement it is about, every unclaimed placement is listed and
carries its census note, no other placement carries anything this run put there,
and a second run changes nothing.

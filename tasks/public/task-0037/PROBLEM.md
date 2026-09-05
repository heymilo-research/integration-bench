# Fix the Placemint redeployment sync

**From:** Integrations / Delivery Ops
**Vendor:** Placemint
**Surface:** polling, writeback
**Category:** fix · **Tier:** 2

## Context

Meridian Talent Group's ATS records a redeployment — a candidate coming off one
assignment and onto another — as one card movement with two ends. The nightly
export in `input/redeployments.csv` has a row per movement: the placement the
candidate left and the status it should end up on, the placement they joined
with its status and fee, when it happened, and the reason the consultant typed.
This connector pushes those into Placemint, which is what Finance reads.

It has run every night for two years. Finance's quarter-end reconciliation has
just come back and Yannick has escalated:

> "The reconciliation will not balance. I have placements sitting closed with
> nobody opened against them, and candidates whose old assignment has been
> reopened and closed again by this thing weeks after the desk had already
> finished with it. The log says most of these rows were refused, so I do not
> understand how anything got written at all — and where it did write, half the
> time there is no note, so nobody can tell me why any of it moved."

Delivery Ops keep their own runbook for this job at
`docs/meridian-redeployment-runbook.md`. Full vendor documentation is in
`docs/` — start at `docs/index.md`.

### The rules Delivery Ops settled on

These are ours, not Placemint's.

- **A redeployment lands in full or not at all.** In full means: the leaver
  carries the row's `from_status`, the joiner carries the row's `to_status` and
  `to_fee_amount`, and exactly one note holding the row's `reason` verbatim is
  filed on the **joiner**, authored `redeployments@meridian.test`.
- A row naming a placement Placemint does not hold is `unknown`: logged, nothing
  written.
- A row whose **leaver has already closed** — Placemint has it at `placed` or
  `fell_through` — is one the desk has already dealt with. It is `rejected`:
  nothing written, on either end.
- A row carrying a status that is not one of Placemint's placement stages is
  `rejected`: nothing written, on either end.
- One entry in the log per row of the export, in file order, and a redeployment
  is never applied twice.

## What we need

The harness runs your code exactly as follows — this command is the contract:

```bash
python -m placemint_movement_sync apply-movements
# -> writes the output artifact listed below
```

1. Work out how a row the log calls refused is still changing Placemint.
2. Fix it, keeping the rules above.
3. Re-run; running twice over the same export must leave Placemint exactly as
   the first run left it.

## Output artifacts

- `output/movement_log.json` — `row_count`, `applied_count`, `rejected_count`,
  `unknown_count`, and `movements`: one entry per row of the export, in file
  order, each with its `movement_ref`, `from_placement_id`, `to_placement_id`,
  `outcome` (`applied`, `rejected` or `unknown`), the `from_stage`, `to_stage`
  and `to_fee_amount` written, the `note_id` Placemint gave the note, and the
  `source_line` it came from.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Vendor sandbox base URL (e.g. `http://vendor:8000`) |
| `PM_CLIENT_ID` | Vendor credential injected by the test harness |
| `PM_CLIENT_SECRET` | Vendor credential injected by the test harness |
| `REDEPLOYMENTS_FILE` | Path to the ATS's nightly redeployment export |
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

The entry command exits 0, every redeployment the rules allow has landed on both
its placements with the note the export asks for, nothing the rules exclude
carries anything this run put there, the log accounts for each row exactly once,
and a second run changes nothing.

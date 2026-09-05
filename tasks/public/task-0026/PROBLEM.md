# Push the Q1 close back into Placemint

**From:** Integrations / Revenue Ops
**Vendor:** Placemint
**Surface:** polling, writeback
**Category:** build · **Tier:** 3

## Context

When Finance closes a quarter, its two exports have to land back in Placemint
so account managers see the invoiced numbers. The files are exactly what the
billing tool produces:

- `input/invoices.csv` — one row per invoice: `invoice_ref`, `period`,
  `fee_pct` (the commission rate for that engagement), `status`, `issued_at`.
- `input/placement_lines.csv` — one row per placement being closed:
  `line_ref`, `invoice_ref`, `placement_id`, `base_salary`, `close_reason`.

Revenue Ops keep their own runbook for this job at
`docs/meridian-quarter-close-runbook.md`.

### Revenue Ops' rules

These are ours, not Placemint's.

- **A line is only ever written if its invoice is `issued`.** A line whose
  `invoice_ref` is `draft`, `void`, or absent from `invoices.csv` altogether is
  **held**: nothing is written for it at all, this quarter or ever.
- **A line naming a placement Placemint has retired is `retired`**, and nothing
  is written for it. Meridian does not invoice a retired placement.
- **A line naming a placement Placemint has never issued is `unknown`**, and
  nothing is written for it.
- Everything else is **applied**, and applying a line means:
  - `close_reason` of `hired` → the placement's stage becomes `placed` and its
    fee becomes `base_salary × fee_pct ÷ 100`, rounded to two decimal places.
  - `close_reason` of `withdrawn` or `declined` → the placement's stage becomes
    `fell_through` and its fee becomes `0`. No commission is earned on a
    placement that did not complete, whatever the salary said.
  - the placement also gets one note, authored by `billing@meridian.test`, whose
    body is the invoice ref, the close reason and the fee to two decimal places,
    separated by single spaces — e.g. `INV-0000-00 hired fee 0.00`.
- A placement is named by at most one line, and a line names exactly one
  placement.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## What we need

The harness runs your code exactly as follows — this command is the contract:

```bash
python -m placemint_quarter_close close-quarter
# -> writes the output artifact listed below
```

1. Read both exports and decide every line.
2. Settle the applied lines against Placemint — the fee, the stage and the note.
3. Write the report described below.

Running the command a second time over the same two files must leave Placemint
exactly as the first run left it.

## Output artifacts

- `output/close_report.json` — `line_count`, `applied_count`, `held_count`,
  `retired_count`, `unknown_count`, and `lines`: one entry per row of
  `placement_lines.csv`, in file order, each with its `line_ref`,
  `invoice_ref`, `placement_id`, `outcome` (`applied`, `held`, `retired` or
  `unknown`), and — for an applied line — the `fee_amount` and `stage` that were
  written and the `note_id` Placemint gave the note.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Vendor sandbox base URL (e.g. `http://vendor:8000`) |
| `PM_CLIENT_ID` | Vendor credential injected by the test harness |
| `PM_CLIENT_SECRET` | Vendor credential injected by the test harness |
| `INVOICES_FILE` | Path to the invoice export |
| `LINES_FILE` | Path to the placement-line export |
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

The entry command exits 0, every line in the report carries the outcome Revenue
Ops' rules give it, every applied placement carries the fee, the stage and the
one note the close file says it should, every line that was not applied has left
its placement exactly as it was, and a second run changes nothing.

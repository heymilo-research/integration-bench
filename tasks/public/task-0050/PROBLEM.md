# Fix the Placemint fee-corrections connector

**From:** Integrations / Revenue Ops
**Vendor:** Placemint
**Surface:** writeback
**Category:** fix · **Tier:** 2

## Context

Meridian Talent Group pushes Finance's fee corrections into Placemint: one row
per correction in `input/corrections.csv`, each naming a placement, the
`role_title` and `fee_amount` it should end up carrying, who approved the
change, and the `reason` the client gave. Every correction becomes an update on
the placement plus one note holding the reason.

The connector has run every month for two years. Since Finance moved onto their
new reporting tool it has been going wrong, and Fahmida in Revenue Ops has
escalated:

> "I signed off forty-two corrections and Placemint has taken eight. The run
> says it finished, exit code and all, but the log does not reconcile with the
> export and corrections I can see in the file are absent from Placemint.
> Nothing fails loudly. It just quietly does a fraction of the work and tells
> me it is done."

Revenue Ops keep their own runbook for this job at
`docs/meridian-fee-corrections-runbook.md`. Full vendor documentation is in
`docs/` — start at `docs/index.md`.

### The rules Revenue Ops settled on

These are ours, not Placemint's.

- Every correction in the export is to be applied: the placement ends up
  carrying the export's `role_title` and `fee_amount`, and one note whose body
  is the export's `reason` **exactly as written**, authored by
  `corrections@meridian.test`.
- A correction naming a placement Placemint has never issued is `unknown` —
  logged, nothing written.
- **A correction we cannot act on with confidence is `rejected`: nothing is
  written for it.** Half a correction on a live placement is worse than none.
- One entry in the log per correction, and a correction is never applied twice.

## What we need

The harness runs your code exactly as follows — this command is the contract:

```bash
python -m placemint_fee_corrections apply-corrections
# -> writes the output artifact listed below
```

1. Work out why the run is doing a fraction of the corrections.
2. Fix it, keeping the rules above.
3. Re-run; running twice over the same export must leave Placemint exactly as
   the first run left it.

## Output artifacts

- `output/correction_log.json` — `row_count`, `applied_count`,
  `rejected_count`, `unknown_count`, and `corrections`: one entry per
  correction, in export order, each with its `correction_ref`, `placement_id`,
  `outcome` (`applied`, `rejected` or `unknown`), the `role_title` and
  `fee_amount` written, the `note_id` Placemint gave the note, and the
  `source_line` it came from.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Vendor sandbox base URL (e.g. `http://vendor:8000`) |
| `PM_CLIENT_ID` | Vendor credential injected by the test harness |
| `PM_CLIENT_SECRET` | Vendor credential injected by the test harness |
| `CORRECTIONS_FILE` | Path to Finance's corrections export |
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

The entry command exits 0, every correction in the export has landed on its
placement with the values and the note the file asks for, the log accounts for
each one exactly once and for nothing else, no placement carries a value the
export never asked for, and a second run changes nothing.

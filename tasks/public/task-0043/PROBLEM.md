# The rota warehouse is counting the same night more than once

**From:** Data Platform / Marchfield Care Group
**Vendor:** Rosterly
**Surface:** polling
**Category:** fix · **Tier:** 2

## Context

The nightly pass in `repo/` mirrors Rosterly changes into a ledger and leaves a
watermark for tomorrow. The downstream loader reads that ledger and nothing
else. Data Platform's runbook is at `docs/marchfield-rota-sync-runbook.md`.

Capacity Planning opened a ticket on Monday. Quoting them:

> Our headcount for last week is wrong and it is wrong in a way we cannot
> unpick. The same shifts are in the extract several times over, and the count
> goes up again every night even on nights when nothing on the rota moved. One
> of the interviews is in there three times.

Separately, Rostering have been chasing us about leavers:

> We took four people off the rota a fortnight ago and cancelled three shifts,
> and every one of them is still showing as active in your reports. Nobody has
> ever been taken out of the warehouse as far as I can tell.

Please find and fix it. The ledger feeds a capacity model that the regional
staffing budget is set from, and the loader cannot take a row back once it has
seen one.

### The rules we work to

These are ours, not Rosterly's.

- **One ledger row per change.** A change Rosterly reports is written once, on
  the pass that first saw it, and never again.
- **`change` is `upsert` or `delete`.** A record that Rosterly no longer holds
  against the rota is a `delete`, and its id goes on that pass's `removed` list
  — that is the list the loader tombstones from.
- **`updated_at_utc` is the instant of the change, in UTC**, formatted
  `YYYY-MM-DDTHH:MM:SSZ`.
- **The first pass starts at `SYNC_SINCE`**, which is when the warehouse was
  loaded from Rosterly's dump. Every later pass starts where the previous one
  left off; the pass carries that between runs in `STATE_DIR`.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## What we need

The harness runs your code exactly as follows — this command is the contract:

```bash
python -m marchfield_rota_sync
# -> one pass; writes the output artifacts listed below
```

The pass is run once a night, several nights running, against the same
`STATE_DIR`.

## Output artifacts

Both are rewritten on every pass and show everything the warehouse has been
handed to date.

- `output/import_report.csv` — header row plus one row per change:
  `run` (which pass wrote the row), `entity` (`worker`, `shift` or
  `interview`), `record_id`, `change`, `updated_at_utc`.
- `output/result.json` — `run_count`, `ledger_row_count`,
  `distinct_record_count`, and `runs`: one entry per pass run so far, each with
  `run`, `watermark_in`, `watermark_out`, `emitted` (the record ids that pass
  wrote), `removed`, `upserts` and `deletes`.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Vendor sandbox base URL (e.g. `http://vendor:8000`) |
| `RY_CLIENT_ID` | Vendor credential injected by the test harness |
| `RY_CLIENT_SECRET` | Vendor credential injected by the test harness |
| `SYNC_SINCE` | When the warehouse was loaded; where the first pass starts |
| `STATE_DIR` | Directory the pass carries state in between runs |
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

The entry command exits 0 on every pass, each change Rosterly reports appears in
the ledger exactly once against the pass that first saw it, everything Rosterly
has taken off the rota is on that pass's `removed` list, and a run of passes
leaves the warehouse holding one row per change and no more.

# Keep the RecruitOS pipeline and the Placemint ledger in step

**From:** Integrations / Revenue Operations
**Vendor:** RecruitOS, Placemint
**Surface:** polling, writeback
**Category:** build · **Tier:** 2

## Context

Recruiters at Northgate work the pipeline in RecruitOS. Everything finance and
the client portal see comes out of Placemint. Nobody has joined the two up, so
Revenue Ops keep a crosswalk by hand — `input/placement_links.csv`, one line per
requisition we bill for — and this job stops the two drifting apart. Our
RecruitOS tenant is read-only; Placemint is where we write.

One invocation is one cycle. Both systems can move between cycles, so each
cycle has to account for both. The repository contains the first reconciler and
the state it carries between cycles.

Revenue Ops keep their own runbook for this job at
`docs/northgate-placement-sync-runbook.md`.

### The rules Revenue Ops settled on

These are ours, not RecruitOS's and not Placemint's.

- A RecruitOS application stage maps to a Placemint placement stage like this:
  `applied` → `sourced`, `interview` → `interviewing`, `offer` → `offered`,
  `hired` → `placed`, `rejected` → `fell_through`.
- Every crosswalk line gets exactly one outcome per cycle, and the outcome
  vocabulary is closed:
  - `unlinked` — Placemint does not hold that placement. Report it for Ken and
    carry on; a line we cannot act on is not a failed cycle.
  - `retired` — Placemint holds it but the requisition has been closed out.
    Closed-out requisitions are finished business: nothing about them goes back
    to Placemint.
  - `inbound` — a Placemint account manager moved the placement themselves
    since our last cycle. Their move is the authoritative one for this cycle
    and the ATS stage is not pushed over it.
  - `pushed` — none of the above, and the mapped ATS stage is not the stage
    Placemint holds, so the mapped stage goes to Placemint.
  - `in_sync` — none of the above and nothing to do.
- The first cycle has no previous cycle, so nothing counts as `inbound` on it.
- `STATE_DIR` is ours and survives between cycles. `OUTPUT_DIR` does not.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## What we need

The harness runs your code exactly as follows — this command is the contract:

```bash
python -m northgate_placement_sync
# -> writes the three output artifacts listed below
```

Run once, that is cycle 1. Run again, that is cycle 2, and so on.

## Output artifacts

- `output/result.json` — `cycle`, `links`, `counts` (one integer per outcome
  name above), `ats_watermark` and `marketplace_watermark` (the positions this
  cycle is leaving behind, as strings; empty is allowed).
- `output/import_report.csv` — header row plus one row per crosswalk line, **in
  crosswalk order**, with columns `application_id`, `placement_id`, `outcome`,
  `ats_stage` (the stage RecruitOS holds), `target_stage` (that stage mapped),
  and `resulting_stage` (the stage Placemint holds for that placement when the
  cycle ends; empty for `unlinked`).
- `output/writeback_log.json` — `cycle` and `writes`: one entry per write this
  cycle sent to Placemint, each with `placement_id`, `stage`, `status` and
  `idempotency_key`.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | RecruitOS sandbox base URL |
| `PLACEMINT_BASE_URL` | Placemint sandbox base URL |
| `RO_CLIENT_ID`, `RO_CLIENT_SECRET` | RecruitOS credentials from the harness |
| `PM_CLIENT_ID`, `PM_CLIENT_SECRET` | Placemint credentials from the harness |
| `CROSSWALK_FILE` | Path to Ken's crosswalk |
| `OUTPUT_DIR` | Directory where output artifacts land |
| `STATE_DIR` | Directory that survives between cycles |

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

The entry command exits 0 on every cycle, each cycle's report accounts for every
crosswalk line with the right outcome and stages, and Placemint ends each cycle
holding the stage the rules above say it should for every linked placement.

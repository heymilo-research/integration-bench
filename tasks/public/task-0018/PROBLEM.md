# Rebuild the nightly requisition rollup on the RecruitOS API

**From:** Integrations / Data Services
**Vendor:** RecruitOS
**Surface:** polling
**Category:** migrate · **Track:** python · **Tier:** 2

## Context

Sandhurst Recruitment bills off one file. Every night a small job produces
`rollup.csv` — one line per application in our RecruitOS tenant — and at 05:20
Finance's revenue loader reads it and runs the invoice cycle. Nobody in
Finance is going to change that loader for us, so the file's columns and its
one-line-per-application shape are fixed.

The file was never really ours. RecruitOS's Reporting Mart, their nightly
extract product, dropped it into a directory and our job renamed a couple of
columns on the way past. RecruitOS have now retired the Reporting Mart: no
drop arrived last night and none is coming. The job needs to produce the same
file off the live API instead, in time for month end.

Data Services owned this before the team was wound down. Their handover note
is in `docs/sandhurst-mart-handover.md` and the code they left is what is in
`src/`. Nobody here has worked on it before.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

### The rules Finance settled on

These are ours, not RecruitOS's.

- The rollup covers **every application in the tenant**, not a delta.
- Each line gets exactly one `disposition`, and the vocabulary is closed.
  Take the first one that applies, in this order:
  - `dropped` — the candidate or the requisition on that line has been
    retired. Retired business is not billable and never becomes billable
    again.
  - `placed` — the application reached `hired`.
  - `lost` — the application reached `rejected`.
  - `frozen` — the requisition the application is against is not `open`.
  - `working` — none of the above.
- `last_change_at` is the most recent moment anything on the line changed:
  the application itself, the requisition it is against, or the candidate.
  ISO 8601, exactly as RecruitOS spells it.
- `retired` in `result.json` is how many candidates and how many requisitions
  the tenant has retired — the counts, not the ids. The on-call dashboard
  trends them.

## What we need

The harness runs your code exactly as follows — this command is the contract:

```bash
python -m sandhurst_rollup sync
# -> writes the two output artifacts listed below
```

## Output artifacts

- `output/rollup.csv` — header row
  `application_id,candidate_id,requisition_id,stage,disposition,last_change_at`,
  then one line per application. `stage` is the application's stage string
  as RecruitOS reports it; the id columns carry RecruitOS's own ids verbatim.
- `output/result.json`:

  ```json
  {
    "source": "<where tonight's rows came from>",
    "counts": {"rows": 0, "dropped": 0, "placed": 0, "lost": 0,
               "frozen": 0, "working": 0},
    "retired": {"candidates": 0, "requisitions": 0},
    "rows": [
      {"application_id": "XX-0000", "candidate_id": "YY-0000",
       "requisition_id": "ZZ-0000", "stage": "somestage",
       "disposition": "working", "last_change_at": "0000-00-00T00:00:00Z"}
    ]
  }
  ```

  `rows` carries the same lines as the CSV. `counts` is the line count plus
  one tally per disposition.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | RecruitOS sandbox base URL (e.g. `http://recruitos:8000`) |
| `MART_DROP_DIR` | Directory the Reporting Mart used to drop its nightly file into |
| `OUTPUT_DIR` | Directory where output artifacts land (defaults to `./output`) |
| `RO_CLIENT_ID` | Vendor credential injected by the test harness |
| `RO_CLIENT_SECRET` | Vendor credential injected by the test harness |

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

Tonight's `rollup.csv` carries every application in the tenant with the right
disposition and the right `last_change_at`, `result.json` agrees with it line
for line, and the tallies Finance and the on-call dashboard read are right.

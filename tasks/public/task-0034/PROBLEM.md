# Weekly crew top-up is putting people on the gate twice

**From:** Integrations / Workforce Platform
**Vendor:** CrewCall
**Surface:** polling, writeback
**Category:** harden · **Track:** python · **Tier:** 3

## Context

Ironvale Stadium Services staff their match days through Fairweather Crewing.
Every Sunday night the agency sends over the week's placement list and our job
puts each of those people on our CrewCall tenant, because the stewards' gate
scanners resolve a worker id and nothing else. The job has been running since
last season and it exits clean every week.

It is being moved onto a much larger stadium next month, and before that happens
the ops lead wants the thing gone over. Two things came in from the desk after
last Sunday's run and neither has an explanation yet:

> "Marisol scanned in on Saturday as a brand new starter with no history and no
> pay band, and the old record is still sitting there under her name. Rota has
> her twice now and I can't tell the two apart."

> "Three of the returners from last winter never made it onto the tenant at all.
> The report says they were fine. They were turned away at the gate."

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## What we need

The same job, safe to run against a tenant several times this size.

Two rules the desk works to, which are ours and not CrewCall's:

- A crew member is the CrewCall worker holding the address on the agency's line,
  compared case-insensitively. That is the only identity the agency and we
  share.
- A worker CrewCall no longer holds is not that crew member. If somebody the
  tenant has let go turns up on the placement list again, they are a new signup:
  the stewards cannot scan a badge we no longer own.

Every placed crew member must come out of the run as **exactly one** worker on
the tenant, and every row of the agency's file must carry the id that crew
member ends up with.

The test harness runs your code exactly as follows — this command is the
contract:

```bash
python -m ironvale_topup
```

## Output artifacts

- `output/placement_report.csv` — one row per row of the agency's file, in file
  order, with the columns `placement_ref,shift_date,crew_email,worker_id,outcome`.
  `outcome` is `matched` or `created`.
- `output/topup_summary.json` — `row_count`, `person_count`, `matched_count`,
  `created_count`, `roster_rows_seen`, and `people`: one entry per crew member
  the file places, carrying `person_key`, `crew_email`, `crew_name`,
  `placement_refs`, `outcome` and `worker_id`.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Vendor sandbox base URL (e.g. `http://vendor:8000`) |
| `CC_API_KEY` | Vendor credential injected by the test harness |
| `INPUT_FILE` | The agency's weekly placement file |
| `OUTPUT_DIR` | Directory where output artifacts land (defaults to `./output`) |
| `PAGE_LIMIT` | Page size the roster sweep asks for |

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

The run exits 0, both artifacts are written, and the tenant holds each placed
crew member exactly once.

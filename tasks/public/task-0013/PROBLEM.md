# The warehouse and RecruitOS have been out of step for months

**From:** Data Platform / Integrations
**Vendor:** RecruitOS
**Surface:** polling, webhooks
**Category:** harden · **Tier:** 3

## Context

Everything downstream of recruiting at Harbour reads the warehouse. Reporting,
the exec dashboard, the weekly pipeline review — none of them talk to RecruitOS.
The nightly pass in this repo is the only thing that puts RecruitOS data into
the warehouse, and it has run unattended since the spring.

Last week a hiring manager asked why a candidate she placed in March still shows
as screening. We spot-checked forty rows against RecruitOS by hand and nine were
wrong. We have no idea how long it has been like that, or how much more of the
file is wrong.

The connector runs, exits 0 and writes its report every night, and has been
quietly wrong for months. That is what you are fixing. The file it reads,
`input/warehouse_mirror.json`, is exactly what last night's run left behind — we
have not cleaned it up, because the pass has to cope with whatever it finds.

The Data Platform desk note for this job is at
`docs/harbour-parity-desk-note.md`.

### The rules

These are ours, not RecruitOS's.

- The warehouse holds one row per candidate, job and application, **live records
  only**, keeping one value for each: `status` for candidates and jobs, `stage`
  for applications. That value is what "in step" means.
- Every row where the warehouse and RecruitOS are not in step gets exactly one
  line in the report, and the vocabulary is closed:
  - `drop` — the warehouse holds an id RecruitOS has never issued.
  - `add` — RecruitOS serves the record, the warehouse does not hold it.
  - `remove` — the warehouse holds it and RecruitOS no longer serves it live.
  - `update` — both hold it and the value differs.
- A record that is already in step gets no line.
- The census we hand the Data Platform is the number of live records the
  warehouse holds for each collection once the pass is finished.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## What we need

The harness runs your code exactly as follows — both commands are the contract:

```bash
python -m harbour_parity_probe serve   # the listener, long-running
python -m harbour_parity_probe sync    # one nightly pass
```

## Output artifacts

- `output/import_report.csv` — header row plus one row per divergence, sorted by
  `entity` then `record_id`, with columns `entity` (`candidate`, `job` or
  `application`), `record_id`, `divergence` (one of the four names above),
  `mirror_value` (what the warehouse held; empty for `add`) and `vendor_value`
  (what RecruitOS serves for it as a live record; empty for `remove` and
  `drop`).
- `output/result.json` — `source`, `snapshot_synced_through`, `divergences`
  (one integer per divergence name) and `census` (one integer per collection).
- `output/events.json` — `applied`: one entry per webhook event the listener
  accepted, each with `event_id`, `event` and `entity_id`.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | RecruitOS sandbox base URL |
| `RO_CLIENT_ID`, `RO_CLIENT_SECRET` | RecruitOS credentials from the harness |
| `RO_WEBHOOK_SECRET` | Webhook signing secret from the harness |
| `SNAPSHOT_FILE` | Last night's warehouse file |
| `OUTPUT_DIR` | Directory where output artifacts land |
| `STATE_DIR` | Directory shared between the listener and the pass |
| `SERVE_HOST`, `SERVE_PORT` | Where the listener binds |

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

`sync` exits 0, the report accounts for every row on which the warehouse and
RecruitOS disagree and for no row on which they agree, the census is the live
count the warehouse ends up holding, and the events the listener accepted are
the ones RecruitOS signed.

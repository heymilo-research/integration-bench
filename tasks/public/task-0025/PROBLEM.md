# Reset the CrewCall roster watermark after the tenant rebuild

**From:** Integrations / Workforce Operations

**Vendor:** CrewCall

**Surface:** polling

**Category:** fix · **Track:** python · **Tier:** 3

## Context

Workforce Operations uses the CrewCall worker roster to prepare dispatch
coverage and headcount reports. The tenant was rebuilt after an agency
consolidation, and CrewCall began issuing worker ids again from the low end of
its normal range. This connector inherited the old tenant generation's
high-water mark. It now exits successfully with an empty report even though
the new tenant contains workers.

The export is read-only: it must not create, update, or delete anything in
CrewCall. The inherited state is in `input/worker-watermark.json`.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## What we need

The test harness runs your code exactly as follows — this command is the
contract:

```bash
python -m crewcall_roster
# -> writes the output artifacts listed below
```

Read the inherited state and reconcile it with the worker roster CrewCall now
serves. A tenant rebuild must produce a complete replacement roster rather than
an empty incremental export, while a normal run against the same tenant must
remain incremental. Leave a watermark that the next run can safely consume.

Write one final report row for each selected worker.
The report must include workers whose source record is marked deleted. Each
report row receives a unique sequential `row_id` beginning at `W-0001`; rows
are ordered by worker `id`. Preserve the source worker fields in the JSON
summary, including `id`, `status`, and `is_deleted`. A run that cannot complete
must exit nonzero.

## Output artifacts

`output/result.json` — an object with an integer `rows` count and a `workers`
array. Each array item contains a generated `row_id` plus the source worker
object:

```json
{
  "rows": 0,
  "previous_watermark": "<previous_worker_id>",
  "reset_performed": true,
  "new_watermark": "<next_worker_id>",
  "workers": [
    {
      "row_id": "W-0001",
      "id": "<worker_id>",
      "status": "<status>",
      "is_deleted": false
    }
  ]
}
```

`output/import_report.csv` — a header row followed by one row per worker:

```text
row_id,id,status
W-0001,<worker_id>,<status>
```

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | CrewCall base URL supplied by the harness |
| `OUTPUT_DIR` | Directory where output artifacts must be written |
| `CC_API_KEY` | CrewCall credential supplied by the harness |
| `WATERMARK_FILE` | JSON state containing `last_worker_id` |

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

The command exits 0, reports the old and replacement watermarks, records that
the regressed watermark was reset, and both artifacts contain every worker in
the rebuilt tenant exactly once. Deleted workers remain represented with their
source marker, and the JSON and CSV reports agree.

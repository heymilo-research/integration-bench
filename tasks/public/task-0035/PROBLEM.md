# Sync Vettly subjects, checks, and reports into our canonical store

**From:** Integrations / Customer Success Engineering
**Vendor:** Vettly (a background-check platform)
**Surface:** polling (pull)

## Context

A background-screening customer is live on Vettly and we need their subjects,
checks, and reports mirrored into our canonical store, kept current by a
periodic polling sync. The first run back-fills everything — 300 subjects,
400 checks, 250 reports, enough volume that a full backfill takes a while to
run; every later run pulls only what changed since the previous pass.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Vettly sandbox base URL |
| `VT_CLIENT_ID` / `VT_CLIENT_SECRET` | OAuth client credentials |
| `OUTPUT_DIR` | where output files go (defaults to `./output`) |

## What we need

The grader runs the connector the same way every time — this is the
contract:

```bash
python -m vettly_sync                # first run: full backfill
python -m vettly_sync --incremental  # later runs: catch-up pass
```

## Output format

Three JSON files under `OUTPUT_DIR`: `subjects.json`, `checks.json`,
`reports.json`. Each is a sorted-by-`source_id` array of:

```json
{"source_id": "sub_0001", "data": { ...record fields... }, "updated_at": ..., "is_deleted": false}
```

Do not change these shapes.

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

A full backfill run and a later incremental run both exit 0. Each writes
subjects, checks, and reports that match the tenant's actual upstream state
at that point — tombstones retained, `completed_at` populated for finished
reports — and the incremental run reflects only what changed since the
prior pass, with no rows missing, duplicated, or re-fetched wholesale.

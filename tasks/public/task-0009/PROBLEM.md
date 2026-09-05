# Migrate off the legacy Bullpen app-token integration

**From:** Integrations / Platform Reliability
**Vendor:** Bullpen (enterprise ATS)
**Surface:** polling (pull)

## Context

Our Bullpen connector (`repo/`, package `bullpen_migrate`) has been polling
candidates, jobs, and applications for a while using Bullpen's **legacy
`X-BP-App-Token`** static-header auth. It works today and has already built
up incremental state (`output/state.json`'s per-entity watermark).

Bullpen has told every tenant this route is being sunset in favor of OAuth
client-credentials (`BP_CLIENT_ID` / `BP_CLIENT_SECRET`). The cutover is
controlled by the vendor; we do not get to choose its timing and will not be
told in advance. Your job is to make the connector survive the sunset: it must
keep the tenant synced correctly across the transition, without losing the
incremental state already built up.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Bullpen sandbox base URL (e.g. `http://vendor:8000`) |
| `BP_CLIENT_ID` / `BP_CLIENT_SECRET` | OAuth client-credentials |
| `BP_APP_TOKEN` | legacy static app-token (works until the tenant's cutover) |
| `OUTPUT_DIR` | where output files go (defaults to `./output`) |

## What we need

The grader runs your package the same way every time — this is the contract:

```bash
python -m bullpen_migrate
```

## Output format

Each of `candidates.json` / `jobs.json` / `applications.json` is a JSON array
of canonical rows sorted by `source_id`: `{"source_id", "data" (all fields
except id/source_id, timestamps normalized to canonical UTC "...Z", the
pipeline field on applications always called `stage`), "is_deleted",
"updated_at"}`. `state.json` holds the current `auth_mode` and per-entity
watermark. Do not change these shapes.

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

The grader's runs — before the tenant's cutover, immediately after, and
later incremental passes — all exit 0 and the output matches the tenant's
actual upstream state at each point, in the canonical shapes above, with
the connector's idempotent, incremental contract intact across the auth
swap — no record missed, duplicated, or corrupted by the migration.

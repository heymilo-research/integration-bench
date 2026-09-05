# Sync StaffLine candidates, jobs, applications, and notes into our canonical store

**From:** Integrations / Customer Success Engineering
**Vendor:** StaffLine (legacy staffing ATS)
**Surface:** polling (pull)

## Context

A staffing customer just went live on StaffLine and we need their data flowing
into our canonical store so the rest of the product (search, dedupe,
reporting) can use it: a one-time back-fill of everything that exists today,
plus an ongoing catch-up pass that keeps us current as records change
upstream. StaffLine has no webhooks, so polling is this integration's only
freshness mechanism.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## What we need

The grader runs your package exactly two ways — these commands are the
contract:

```bash
# Full back-fill
python -m staffline_sync

# Incremental catch-up
python -m staffline_sync --incremental
```

## Canonical output shape

One JSON file per entity kind under `OUTPUT_DIR`, each a JSON array of rows
sorted by `source_id`:

| Key | Meaning |
|---|---|
| `source_id` | the StaffLine record id |
| `data` | every other field as returned by the API |
| `updated_at` | StaffLine's own modification timestamp for the row |
| `is_deleted` | tombstone flag |

Don't change this shape — the downstream consumer already depends on it.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | StaffLine sandbox base URL (e.g. `http://vendor:8000`) |
| `SL_APP_TOKEN` / `SL_HMAC_SECRET` | this tenant's application credentials |
| `DATABASE_URL` | SQLite URL backing the canonical store (`sqlite:////data/canonical.db`) |
| `OUTPUT_DIR` | where the JSON output files land (defaults to `./output`) |

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

A fresh back-fill populates all four canonical files from StaffLine's current
state, and — after StaffLine has accumulated its own edits, creates, and
deletes — a later incremental pass converges all four files to match, with
both `python -m staffline_sync` and `python -m staffline_sync --incremental`
exiting 0.

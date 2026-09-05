# Harden the Vettly sync connector for production scheduling

**From:** Integrations / Reliability Engineering
**Vendor:** Vettly (background-check platform)
**Surface:** polling (pull)

## Context

A Vettly customer needs their subjects, checks, and reports kept current in
our canonical store. We already have a connector for this — it authenticates
against Vettly, crawls all three collections, and keeps them up to date on
later passes.

The scheduler that runs this sync is redeployed on its own cadence and does
not coordinate with us, so a run may be terminated at any point. Ops needs
the customer's data to end up complete and correct however often that
happens, and the sync to stay within its normal time budget on every pass.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Vettly sandbox base URL |
| `VT_CLIENT_ID` / `VT_CLIENT_SECRET` | OAuth2 client credentials |
| `DATABASE_URL` | sqlite URL for the canonical store |
| `OUTPUT_DIR` | where output files go (defaults to `./output`) |

## What we need

The grader runs your package the same way every time — this is the
contract:

```bash
python -m vettly_sync sync   # one sync pass over subjects, checks, reports
python -m vettly_sync dump   # snapshot the canonical store to $OUTPUT_DIR
```

## Output format

`dump` writes `$OUTPUT_DIR/{subjects,checks,reports}.json`: JSON arrays of
canonical rows sorted by `source_id`, each `{source_id, data, updated_at,
is_deleted}` — `data` holds every field except `id`/`source_id`, with the
report's completion timestamp mapped to `data.completed_at`. Do not change
this shape or the mapping.

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

A `sync` run, terminated and re-run at any point any number of times, still
leaves the canonical store exactly matching the tenant's actual upstream
state — no rows missing, none duplicated — and a `dump` right after confirms
it.

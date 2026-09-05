# Harden the CrewCall snapshot connector for production

**From:** Integrations / Customer Success Engineering
**Vendor:** CrewCall (high-churn temp-staffing marketplace)
**Surface:** polling (pull)

## Context

We already have a CrewCall connector that snapshots workers, gigs, and
assignments faithfully against a quiet sandbox. The first production exports
were missing records visible in CrewCall, and one scheduled run exited nonzero.
It needs to be safe to schedule unattended.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | CrewCall sandbox base URL (e.g. `http://vendor:8000`) |
| `CC_API_KEY` | the credential the connector authenticates with |
| `OUTPUT_DIR` | where output files go (defaults to `./output`) |

## What we need

The grader runs your package the same way every time — this is the contract:

```bash
python -m crewcall_sync sync
```

## Output format (unchanged)

Same canonical shape as the existing connector already produces: `source_id`,
`data` (the full raw wire object), `updated_at` (ISO-8601 `Z` string), and
`is_deleted` (tombstone flag). Do not change these shapes.

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

A fresh `python -m crewcall_sync sync` produces the correct, duplicate-free
snapshot of workers, gigs, and assignments at the time of the run, matching
the tenant's actual upstream state and failing clearly rather than publishing
an incomplete result.

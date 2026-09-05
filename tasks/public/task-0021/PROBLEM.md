# Sync GlobalHire candidates, placements, and agencies into our canonical store

**From:** Integrations / Customer Success Engineering
**Vendor:** GlobalHire (multinational staffing-agency platform)
**Surface:** polling (pull)

## Context

A staffing customer runs their pipeline on GlobalHire and wants their full
candidate, placement, and agency data mirrored into our canonical store so
downstream reporting and routing can run off our data instead of live calls
to the vendor. This is a fresh integration for this tenant — there is no
store yet, so a run has to land the complete, correct picture GlobalHire
actually has for all three record types. No partial coverage, no shortcuts.

The `globalhire_sync` package in `repo/` already talks to GlobalHire and
writes output in the shape we need. Treat it as a starting point: extend or
adjust whatever you need to in order to meet the contract below.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | GlobalHire sandbox base URL |
| `GH_API_KEY` | static API key |
| `OUTPUT_DIR` | where output JSON is written (default `./output`) |

## What we need

The grader runs your package like this:

```bash
python -m globalhire_sync
```

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

A fresh run exits `0`, and all three output files match this tenant's actual
upstream state exactly: every candidate, every placement, and every agency
GlobalHire has for this tenant, correctly mapped, with no missing records
and no duplicates.

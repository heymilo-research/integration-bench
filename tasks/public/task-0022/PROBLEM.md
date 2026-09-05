# Harden the GlobalHire backfill for a production tenant

**From:** Integrations / Reliability Engineering
**Vendor:** GlobalHire (multinational staffing-agency platform)
**Surface:** polling (pull)

## Context

We already have a GlobalHire polling connector (`repo/`, package
`globalhire_sync`) that backfills the ~6,000-record candidate list into
our canonical store and keeps it current with incremental catch-ups.
Reliability wants it hardened before we run it against GlobalHire under real
production load.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | GlobalHire sandbox base URL (e.g. `http://vendor:8000`) |
| `GH_API_KEY` | static API key |
| `DATABASE_URL` | sqlite URL for the canonical store |
| `OUTPUT_DIR` | where `dump` writes the snapshot |

## What we need

The grader runs your package exactly two ways:

```bash
python -m globalhire_sync sync
python -m globalhire_sync dump

python -m globalhire_sync sync
python -m globalhire_sync dump
```

## Canonical store shape

`canonical.candidates`:

| column | meaning |
|---|---|
| `source_id` | the GlobalHire candidate id (primary key) |
| `data` | the candidate's fields (jsonb), verbatim from the wire |
| `updated_at` | the candidate's last-modified instant, as **UTC epoch seconds** |
| `is_deleted` | tombstone flag: `true` once the candidate is deleted upstream |

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

A fresh `sync` back-fills every current candidate into a store that
matches the tenant's actual upstream state exactly, and a later `sync`
lands only the changes since the last pass, with no record missed,
duplicated, or corrupted under real vendor conditions.

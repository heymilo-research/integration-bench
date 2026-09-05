# Keep TalentForge candidates fresh: webhooks + reconciliation polling

**From:** Integrations / Customer Success Engineering
**Vendor:** TalentForge (ATS/CRM)
**Surface:** webhooks (push) + polling (pull)

## Context

A large staffing customer is live on TalentForge and needs their candidate
records mirrored into our canonical store, kept fresh in near-real-time. The
connector needs to consume TalentForge's webhooks for freshness and run a
periodic reconciliation poll alongside them, so the canonical store reflects
the customer's true upstream state at all times.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | TalentForge sandbox base URL (e.g. `http://vendor:8000`) |
| `TF_CLIENT_ID` / `TF_CLIENT_SECRET` | OAuth client credentials |
| `TF_WEBHOOK_SECRET` | secret used to verify inbound webhook signatures |
| `DATABASE_URL` | sqlite URL for the canonical store |
| `OUTPUT_DIR` | where `connector dump` writes the canonical snapshot (defaults to `./output`) |

## What we need

The grader drives your package with:

```bash
python -m connector sync    # one polling pass: backfill, or incremental reconcile
python -m connector serve   # webhook listener, POST /webhooks/talentforge
python -m connector dump    # snapshot the canonical store to $OUTPUT_DIR/candidates.json
```

## Canonical store shape

`canonical.candidates`:

| column | meaning |
|---|---|
| `source_id` | the TalentForge candidate id (primary key) |
| `data` | the candidate's fields (jsonb) |
| `updated_at` | the candidate's last-modified timestamp |
| `is_deleted` | tombstone flag: `true` once the candidate is deleted upstream; the row is retained, never removed |

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

A fresh `connector sync`, a live `connector serve` pass, and a later
reconciliation `connector sync` all exit 0, and `connector dump`'s output
matches the customer's actual upstream state at each point, in the canonical
shape above. Applying the same delivery more than once is idempotent, and
conflict resolution is monotonic: neither path may miss, duplicate, or regress
a record.

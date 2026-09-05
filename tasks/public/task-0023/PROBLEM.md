# TalentLoop connector: keep a candidate/application mirror deletion-accurate

**From:** Integrations / Customer Success Engineering
**Vendor:** TalentLoop (event-first modern ATS)
**Surface:** polling (read) and webhooks (push)

## Context

A customer runs TalentLoop as their ATS. We need a canonical mirror of
their candidates and applications that stays accurate when a record is
**deleted** upstream — not just when one is created or updated. The
mirror can't depend on a single discovery path: it must converge whether
the connector learns about a deletion through a consumed webhook event or
through a polling pass with no webhook listener running at all.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | TalentLoop sandbox base URL |
| `TL_CLIENT_ID` / `TL_CLIENT_SECRET` | OAuth2 client credentials |
| `TL_WEBHOOK_SECRET` | HMAC secret for webhook verification |
| `DATABASE_URL` | sqlite URL for the canonical store |
| `OUTPUT_DIR` | where `dump` writes JSON snapshots (default `./output`) |

## Report / outputs

### `output/candidates.json` / `output/applications.json` (written by `dump`)

The canonical mirrors, each a JSON array of
`{source_id, data, updated_at, is_deleted}` rows sorted by `source_id`. A
deleted record appears with `is_deleted: true` in your canonical output.

## Run contract

```bash
python -m talentloop_deletes backfill
python -m talentloop_deletes poll
python -m talentloop_deletes serve [--max-events N] [--idle-timeout S] [--max-runtime S]
python -m talentloop_deletes dump
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

A candidate or application deleted upstream lands as `is_deleted: true`
in the canonical mirror, whether discovered through the webhook path or
through a poll-only run, and every command exits `0` with the mirror
matching the tenant's actual upstream state.

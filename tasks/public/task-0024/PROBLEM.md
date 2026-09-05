# TalentLoop connector: unified freshness across webhook-covered and poll-only entities

**From:** Integrations / Customer Success Engineering
**Vendor:** TalentLoop (event-first modern ATS)
**Surface:** webhooks (push) and polling (read), all four entities

## Context

A customer runs TalentLoop as their ATS. We need a canonical mirror of ALL
FOUR of their entities — candidates, jobs, applications, and notes — that
stays fresh on an ongoing basis, not just at initial setup.

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

### `output/candidates.json` / `output/jobs.json` / `output/applications.json` / `output/notes.json` (written by `dump`)

The canonical mirrors, each a JSON array of
`{source_id, data, updated_at, is_deleted}` rows sorted by `source_id`.

## Run contract

```bash
python -m talentloop_selective backfill
python -m talentloop_selective poll
python -m talentloop_selective serve [--max-events N] [--idle-timeout S] [--max-runtime S]
python -m talentloop_selective dump
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

A later `poll` reflects upstream changes across all four entities — the
mirror stays current on an ongoing basis, not just at the initial
backfill. Webhook-driven freshness holds under real delivery conditions,
with a tampered delivery never touching the store. A deletion in any of
the four entities lands as `is_deleted: true` in the canonical mirror,
regardless of which discovery path finds it first.

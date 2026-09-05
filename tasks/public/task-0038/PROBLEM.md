# TalentLoop connector: make webhook-driven freshness durable against a misbehaving receiver

**From:** Integrations / Customer Success Engineering
**Vendor:** TalentLoop (event-first modern ATS)
**Surface:** webhooks (push) and polling (read)

## Context

A customer runs TalentLoop as their ATS. Our connector keeps our platform's
mirror of their candidates, jobs, applications, and notes current. The
business requirement is non-negotiable: no candidate or application
change — a pipeline update, a note left after an interview, a withdrawn
application — may go missing from our mirror, even one our own receiver
mishandles or never gets a chance to see at all. A recruiter acting on stale
data is a customer-visible failure, not an edge case we can shrug off.

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
python -m talentloop_reliable backfill
python -m talentloop_reliable poll
python -m talentloop_reliable serve [--max-events N] [--idle-timeout S] [--max-runtime S]
python -m talentloop_reliable recover_missed_events
python -m talentloop_reliable dump
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

Every candidate and application change lands in the canonical mirror,
whether it arrives through the webhook listener or has to be picked up
through the platform's own undelivered-event listing — including a change
whose push attempt never happened at all. By the end of a run, nothing this
tenant's platform still considers undelivered is left outstanding. The
job/note mirror keeps recurring on every poll, not just at initial backfill,
unaffected by any of the above.

# Reliability: Interviewly connector loses and regresses updates

**From:** Integrations / Reliability Engineering
**Vendor:** Interviewly (interview scheduling)
**Surface:** webhooks (push), with a polling backfill on startup

## Context

A customer has been live on our Interviewly connector for a while. Support
has started seeing two related complaints:

1. Some interview updates never show up in our copy of their data at all.
2. More worryingly, a handful of interviews have briefly shown the correct,
   current status and then **reverted to an older one**.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Interviewly sandbox base URL |
| `IV_CLIENT_ID` / `IV_CLIENT_SECRET` | OAuth client credentials (polling backfill) |
| `IV_WEBHOOK_SECRET` | webhook signing secret |
| `SERVE_HOST` / `SERVE_PORT` | webhook listener bind address |
| `OUTPUT_DIR` | where output files go (defaults to `./output`) |

## What we need

```bash
python -m interviewly_relay sync   # polling backfill
python -m interviewly_relay serve  # webhook listener: POST /webhooks/interviewly
```

## Output contract

Everything below is graded by exact match, so keep the shapes exactly as they
are.

`output/interviews.json`, `output/panelists.json`, `output/feedback.json` —
the canonical store: one file per entity kind, a list of rows sorted by
`source_id`, each `{"source_id", "data", "updated_at", "is_deleted"}`.

`output/event_journal.json` — the applied-event journal our support team reads
when a customer disputes a record's history. It is our audit trail of what the
connector *did* with each delivery, so it has to stay a truthful record: **one
entry per webhook event this connector applies**, appended under that record's
`source_id`, in the order the applies happened.

```json
{
  "itv_0042": [
    { "event_id": "evt_00001", "occurred_at": "2026-03-14T11:01:00Z" }
  ]
}
```

Bookkeeping that must survive a restart belongs in durable state rather than
in-memory structures tied to one listener process.

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

After a representative delivery stream, the canonical store exactly matches
Interviewly's actual current state for every interview, panelist, and feedback
record, and the journal is a truthful record of the applies that produced it:
one entry per genuine event, none applied twice, and never an entry older than
one already recorded for the same record. Invalid deliveries are rejected.

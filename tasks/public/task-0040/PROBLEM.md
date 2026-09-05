# Push interview reschedules upstream into Interviewly

**From:** Integrations / Customer Success Engineering
**Vendor:** Interviewly (interview scheduling)
**Surface:** writeback (reschedule) + webhooks (confirmation)

## Context

We need to push interview reschedules from our system into Interviewly using
its documented write operation.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Interviewly sandbox base URL |
| `IV_CLIENT_ID` / `IV_CLIENT_SECRET` | OAuth client credentials |
| `IV_WEBHOOK_SECRET` | webhook signing secret |
| `SERVE_HOST` / `SERVE_PORT` | webhook listener bind address |
| `INPUT_FILE` | staged reschedules to push (defaults to `input/pending_reschedules.json`) |
| `OUTPUT_DIR` | where output files go (defaults to `./output`) |

## What we need

```bash
python -m interviewly_writeback push   # push the staged reschedules
python -m interviewly_writeback serve  # webhook listener + confirmation handling
python -m interviewly_writeback dump   # re-write output files from the current store
```

## Output format

`$OUTPUT_DIR/writeback_result.json` is `{"reschedules": [...]}`, sorted by
`client_ref`, one record per staged item: `client_ref`, `interview_id`,
`requested_scheduled_at`, `idempotency_key` (derive it as
`reschedule:<client_ref>` — downstream reconciliation keys on that exact
format), `status` (`pending`,
`committed`, or `failed`), `confirmation_event_id` (set once confirmed,
otherwise `null`), `ok` (bool), and `error` (the vendor's failure body, or
`null`).

`$OUTPUT_DIR/interviews.json` is the same canonical shape as our other
Interviewly connectors: a `source_id`-sorted array of `{source_id, data,
updated_at, is_deleted}`.

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

Every well-formed staged reschedule reaches `committed` in
`writeback_result.json`, matching what actually landed upstream, exactly
once even under a repeated push of the same batch; the malformed entry is
reported as a failure and never applied.

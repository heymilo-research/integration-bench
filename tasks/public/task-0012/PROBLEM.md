# Build the full TalentLoop connector: polling, webhooks, and writeback

**From:** Integrations / Customer Success Engineering
**Vendor:** TalentLoop (event-first ATS)
**Surface:** polling (pull), webhooks (push), writeback (POST)

## Context

We're bringing TalentLoop on as a full two-way integration: candidates,
jobs, applications, and notes need to stay in sync in both directions, and
this has to hold up in production, not just in a clean demo. The repository
contains the first implementation of all three surfaces (`repo/`, package
`talentloop_summit`) against TalentLoop's sandbox.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

The repository contains a starter implementation spanning authentication,
persistence and the five command surfaces below. Inspect it as a whole; no
module should be assumed correct merely because another component calls it.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | TalentLoop sandbox base URL (e.g. `http://vendor:8000`) |
| `TL_CLIENT_ID` / `TL_CLIENT_SECRET` | OAuth client-credentials |
| `TL_WEBHOOK_SECRET` | webhook signing secret |
| `INPUT_FILE` | staged writeback batch (defaults to `input/pending_writeback.json`) |
| `OUTPUT_DIR` | where output files go (defaults to `./output`) |

## What we need

The grader runs your package the same way every time — this is the contract:

```bash
python -m talentloop_summit backfill   # full crawl, all 4 entities
python -m talentloop_summit serve      # webhook listener
python -m talentloop_summit poll       # incremental sweep — must run repeatedly, not just once
python -m talentloop_summit push       # drain the staged writeback batch
python -m talentloop_summit dump       # write output/*.json for inspection
```

## Output format

`dump` writes `output/{candidates,jobs,applications,notes}.json`, each a
sorted JSON array of canonical rows: `{"source_id", "data", "updated_at",
"is_deleted"}`. `push` writes `output/writeback_result.json`: `{"events":
[...]}`, one entry per staged item (`{"client_ref", "ok", "record"}` on
success, `{"client_ref", "ok": false, "error"}` on failure), sorted by
`client_ref`. Do not change these shapes.

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

`backfill`, `serve`, `poll`, and `push` all exit 0, and after a full sync
cycle the canonical store and writeback output match TalentLoop's actual
upstream state for all four entities — every update and delete correctly
reflected regardless of which surface first revealed it, no duplicate or
missing records, and the declared output and idempotency contracts holding
across repeated runs.

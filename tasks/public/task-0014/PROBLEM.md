# HireWire: clear the stale-screening correction backlog, exactly once

**From:** RevOps Systems
**Vendor:** HireWire (scrappy, fast-moving startup ATS)
**Surface:** writeback (push), with a read side to find the work

## Context

One of our staffing customers runs a housekeeping rule against their
HireWire tenant: any candidate who is still sitting in the `screening`
stage is stale and needs to be closed out. Closing one out means two
things happen against HireWire: an audit event gets logged against the
candidate (so there's a record of why the stage changed), and the
candidate's `stage` is moved to `rejected`.

This correction sync runs periodically against the live tenant. Ops's one
hard requirement: every candidate in the backlog gets corrected **exactly
once** — a candidate that already got its event-plus-stage-change from an
earlier pass must never get a second one, and a candidate that hasn't been
corrected yet must never be skipped. Losing a correction leaves a stale
candidate stuck in the pipeline; double-applying one leaves a duplicate
audit trail against a real person's record. Neither is acceptable.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | HireWire sandbox base URL (e.g. `http://vendor:8000`) |
| `HW_API_KEY` | API key for the HireWire sandbox |
| `OUTPUT_DIR` | where `writeback_result.json` is written (defaults to `./output`) |

## What we need

The grader runs your package the same way every time — this is the
contract:

```bash
python -m hirewire_corrections correct
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

A fresh `python -m hirewire_corrections correct` clears the entire
stale-screening backlog against the tenant, `writeback_result.json`
matches the tenant's actual resulting state for every candidate in it, and
HireWire holds exactly one audit event and one stage change per corrected
candidate — no matter what the write calls' responses looked like along
the way.

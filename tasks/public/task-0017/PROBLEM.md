# Move the Brightmoor candidate mirror onto TalentForge's event subscription

**From:** Platform / Brightmoor Staffing
**Vendor:** TalentForge
**Surface:** polling, webhooks
**Category:** migrate · **Track:** python · **Tier:** 3

## Context

`brightmoor-sync` keeps the warehouse's copy of the TalentForge candidate list
current. It has always done that by walking the whole collection on every pass.
The tenant has grown past that: the walk consumes most of our daily read
allowance, and TalentForge has now provisioned the event subscription.

So the pass has to change shape. It consumes what TalentForge tells us as it
happens, and it also has to produce something the old pass never did: a change
ledger, one row per change we were told about, which the reporting team will
key their daily deltas off.

R. Okonkwo left a cut-over runbook before moving teams; it is in `docs/`
alongside the vendor's own guides. Full vendor documentation is in `docs/` —
start at `docs/index.md`.

The repository contains the old polling pass and its supporting transport and
persistence. Inspect the cut-over path as a whole.

## What we need

The harness runs your code exactly as follows — these two commands are the
contract:

```bash
python -m tf_event_cutover serve
# -> long-running; binds SERVE_HOST:SERVE_PORT and accepts TalentForge's
#    deliveries for the lifetime of the process

python -m tf_event_cutover sync
# -> one pass; writes the output artifacts listed below and exits 0
```

Deliveries reach us only while a `serve` process is up; a `sync` pass is a
separate, one-shot process that shares nothing with it but `OUTPUT_DIR`.

Rules the warehouse imposes, which are ours and not TalentForge's:

- **`mirror.json` is the current state of every candidate the tenant holds** —
  one row each, carrying what TalentForge holds for that person now.
- **`updated_at`, on both artifacts, is UTC ISO-8601 to the second with a
  trailing `Z`** (`YYYY-MM-DDTHH:MM:SSZ`) — the shape every downstream report
  has bucketed on since the file era.
- **`change_ledger.json` is one row per change TalentForge announced to us**,
  carrying the announcement's own identifier and instant plus the candidate's
  values. A change nobody announced does not belong on the ledger.
- **The whole-collection walk is what we are retiring.** Filling a cold mirror
  from the list route once is still how a mirror starts. After that, a pass
  that re-lists the entire collection has not migrated anything.
- `state.json`'s `watermark` is what the ops dashboard reads to decide whether
  the mirror is current, so it has to move with the mirror.

## Output artifacts

- `output/mirror.json` — `{"row_count": <int>, "rows": [...]}`, one row per
  candidate:

  ```json
  {"source_id": "XX-0000", "given_name": "<str>", "family_name": "<str>",
   "email": "someone@example.invalid", "phone": "<str>",
   "pipeline_status": "<str>", "is_deleted": false,
   "updated_at": "2000-01-01T00:00:00Z"}
  ```

- `output/change_ledger.json` — `{"row_count": <int>, "rows": [...]}`:

  ```json
  {"event_id": "evt_00000", "event": "<str>", "candidate_id": "XX-0000",
   "occurred_at": "2000-01-01T00:00:00Z", "pipeline_status": "<str>",
   "updated_at": "2000-01-01T00:00:00Z"}
  ```

- `output/state.json` — `{"watermark": "<str|null>", "runs": <int>}`

Row order does not matter; the loader sorts. Do not change these shapes.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Vendor sandbox base URL (e.g. `http://vendor:8000`) |
| `OUTPUT_DIR` | Directory where output artifacts land (defaults to `./output`) |
| `SERVE_HOST` | Address `serve` binds |
| `SERVE_PORT` | Port `serve` binds |
| `TF_CLIENT_ID` | Vendor credential injected by the test harness |
| `TF_CLIENT_SECRET` | Vendor credential injected by the test harness |
| `TF_WEBHOOK_SECRET` | Vendor credential injected by the test harness |

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

`serve` accepts what TalentForge sends it, a following `sync` exits 0, every
row of `mirror.json` carries the values TalentForge actually holds for that
person, `change_ledger.json` holds exactly one row per announced change with
that candidate's real values on it, and `watermark` reflects how current the
mirror is.

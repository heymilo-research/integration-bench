# HireWire connector: push stage-change events + keep an incremental poll fresh

**From:** Integrations / Customer Success Engineering
**Vendor:** HireWire (a scrappy-startup ATS)
**Surface:** writeback (PATCH + POST) and polling (incremental read)

## Context

We run a two-way integration with HireWire for a staffing customer. Our product
both **reads** candidates out of HireWire on a schedule and **writes**
stage-change activity back into it. A starter connector is in `repo/`; inspect
the complete read and write paths against the contract below.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | HireWire sandbox base URL (e.g. `http://vendor:8000`) |
| `HW_API_KEY` | static API key |
| `INPUT_FILE` | path to the staged writeback batch (defaults to `input/pending_events.json`) |
| `OUTPUT_DIR` | where output files go (defaults to `./output`) |

## Report / outputs

### `output/writeback_result.json` (written by `push`)

```json
{
  "events": [
    {"client_ref": "evt-1", "ok": true,  "candidate": { ...current state of the patched candidate... },
     "event": { ...the created event record... }},
    {"client_ref": "evt-3", "ok": false, "error": {"status": 422, "field_errors": {"event_type": ["is required"]}}}
  ]
}
```

`events` is sorted by `client_ref`. On success, `candidate`/`event` carry the
patched candidate's current state and the created event record; on a rejected
write, `ok` is `false` and `error` carries the vendor's status and
`field_errors` body instead.

### `output/candidates.json` (written by `poll`)

A JSON array of canonical candidate rows, sorted by `source_id`:

```json
[ {"source_id": "cand_0001", "data": { ...raw record... }, "updated_at": 1773482460, "is_deleted": false} ]
```

`source_id` is the candidate id, `data` is the full record HireWire returned,
`updated_at` is the last-modified timestamp as an integer, and `is_deleted`
mirrors the candidate's soft-delete flag (retained as a tombstone row, not
dropped).

## Run contract

```bash
python -m hirewire_connector push   # writes output/writeback_result.json
python -m hirewire_connector poll   # writes output/candidates.json
python -m hirewire_connector dump   # rewrites output/candidates.json from the current canonical store
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

`push`, a repeated `push`, and `poll` (initial backfill and a later
incremental run) all exit 0, with `output/writeback_result.json` and
`output/candidates.json` matching HireWire's actual state at each point — every
staged write landed exactly once with rejections reported rather than fatal
or duplicated, and the candidate store current with no data lost or
re-fetched needlessly.

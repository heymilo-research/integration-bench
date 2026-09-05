# Build a StaffLine full-sync + writeback connector

**From:** Integrations Engineering
**Vendor:** StaffLine (legacy staffing ATS)
**Surface:** polling (pull) + writeback (push)

## Context

We are onboarding a new staffing customer who runs StaffLine, a legacy staffing
ATS. We need a connector that keeps our canonical store in sync with their
StaffLine tenant **and** pushes a batch of changes back into StaffLine.

A starter package (`repo/`, the `staffline_fullsync` Python package) is in place.
Treat the repository as a starting implementation and inspect the whole path
from transport through persistence and output.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## What it must do

### 1. Full sync (`sync`)

```bash
python -m staffline_fullsync sync
```

One `sync` pass must converge our canonical store to StaffLine reality:

- **Candidates and applications** are pulled in full and written to
  `candidates.json` / `applications.json` (keyed by StaffLine id, with the
  record's fields under `data`). A later `sync` after upstream edits/creates must
  reflect those changes; re-running `sync` must be idempotent (converge, not
  duplicate).
- **Deletions become tombstones.** When a record is deleted upstream, the
  matching canonical row must be marked deleted (`is_deleted = true`) with the
  row **retained**, so downstream can reconcile the deletion rather than silently
  losing history.
- **Applications must carry their stage.** Each application row's `data` must
  include its pipeline `stage`.

### 2. Writeback (`writeback`)

```bash
python -m staffline_fullsync writeback
```

There is a fixed batch of pending writes to push
(`src/staffline_fullsync/writeback_requests.py`). Push each one
to StaffLine and write a per-write result list to `writeback.json`:

```json
[
  {"op": "createNote", "candidate_id": "cand_0001", "ok": true,  "id": "note_0081", "err": null},
  {"op": "createNote", "candidate_id": "cand_0002", "ok": false, "id": null,        "err": "MISSING note_text"}
]
```

`ok` must reflect whether the write actually **landed upstream** — not whether
your code expected it to succeed.

## Environment

Your process gets these variables:

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | StaffLine sandbox base URL (e.g. `http://vendor:8000`) |
| `SL_APP_TOKEN` / `SL_HMAC_SECRET` | static application token + HMAC signing secret |
| `OUTPUT_DIR` | where the canonical store is written (defaults to `./output`) |

## Canonical store shape

One JSON file per entity kind under `OUTPUT_DIR`, each a list of rows sorted by
`source_id`:

| key | meaning |
|---|---|
| `source_id` | the StaffLine record id |
| `data` | the record's fields (all fields minus the id) |
| `updated_at` | the StaffLine `mod_ts` for the row |
| `is_deleted` | tombstone flag: `true` once the record is deleted upstream |

The package persists canonical rows and watermarks in `state.json`; do not
change the output shape.

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

- `python -m staffline_fullsync sync` back-fills candidates and applications
  (applications with their stage), and after upstream edits/creates/deletes a
  later `sync` reflects all of them — updates and new records present, deleted
  records tombstoned (row retained, `is_deleted = true`).
- `python -m staffline_fullsync writeback` pushes the pending batch and records a
  correct per-write result in `writeback.json`, matching what StaffLine actually
  did with each write.
- Both commands exit 0, and no credential is ever leaked into a request's query
  string.

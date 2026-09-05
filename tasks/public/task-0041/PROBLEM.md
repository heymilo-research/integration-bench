# Paygrade connector: employee/assignment sync and writeback

**From:** Integrations / Customer Success Engineering
**Vendor:** Paygrade (payroll/HRIS bridge)
**Surface:** polling (pull) and writeback (push)

## Context

We're standing up a new Paygrade integration for a payroll customer. Paygrade
uses an RPC-style API rather than a normal REST surface. We need our canonical
store kept current with Paygrade's `employee` and `assignment` records,
including deletions, and we need to push new assignments and employee updates
back into Paygrade reliably.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Paygrade sandbox base URL |
| `PG_APP_TOKEN` | credential for authenticating requests to Paygrade |
| `INPUT_FILE` | staged writes to push (defaults to `input/pending_writes.json`) |
| `OUTPUT_DIR` | where output files go (defaults to `./output`) |

## What we need

```bash
python -m paygrade_sync sync        # back-fill, then incremental reconcile
python -m paygrade_sync writeback   # push the staged writes
python -m paygrade_sync dump        # re-emit the current canonical store
```

## Output format

### `output/employees.json` / `output/assignments.json` (written by `sync`)

JSON arrays of canonical rows, sorted by `source_id`:

```json
[{"source_id": "emp_0001", "data": {...record fields...}, "updated_at": ..., "is_deleted": false}]
```

- `data` is the record's own fields as Paygrade returns them.
- `updated_at` reflects the record's own last-modified time.
- `is_deleted` becomes `true` once a deletion has been observed for that
  id; the row is retained, not removed.

### `output/writeback_result.json` (written by `writeback`)

```json
{
  "writes": [
    {"client_ref": "wb-1", "ok": true, "result": {...}},
    {"client_ref": "wb-2", "ok": false, "error": {...}}
  ]
}
```

- `writes` is sorted by `client_ref`.
- A successful write's `result` must be the record you independently
  re-fetch after the write, not whatever the write call itself echoed back.
- A failed write's `error` is the vendor's own failure body.

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

A fresh `sync` back-fills every current employee and assignment; a later
`sync` after upstream changes, including deletions, reflects them correctly
with nothing missed or duplicated; and `writeback` applies every
well-formed staged write exactly once, correctly distinguishing a genuine
success from a failure in the output above.

# Bulk-import a migration batch into StaffLine

**From:** Integrations / Customer Success Engineering
**Vendor:** StaffLine (legacy staffing ATS)
**Surface:** writeback (bulk create)

## Context

A staffing customer is migrating a batch of candidates out of a spreadsheet
and into StaffLine. They want the batch imported in bulk rather than one
record at a time, and — because this feeds their own reporting — they need
our canonical store to reflect exactly which candidates actually ended up in
StaffLine, not just which ones we asked StaffLine to create. StaffLine
shipped a bulk import endpoint for exactly this kind of migration.

The batch to import is staged in `repo/input/candidate_batch.json`: a JSON
list of pending candidates, each with a stable `client_ref` (our own handle
for that logical record) plus the candidate's fields.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | StaffLine sandbox base URL (e.g. `http://vendor:8000`) |
| `SL_APP_TOKEN` / `SL_HMAC_SECRET` | static application token + HMAC signing secret |
| `DATABASE_URL` | sqlite URL for the canonical store |
| `INPUT_FILE` | path to the staged batch (defaults to `./input/candidate_batch.json`) |
| `OUTPUT_DIR` | where output files go (defaults to `./output`) |

## What we need

The grader runs your package the same way every time — this is the contract:

```bash
python -m staffline_bulk push
python -m staffline_bulk dump
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

A `push` against the staged batch produces `bulk_result.json` with every
item accounted for exactly once, `created` reflecting each record's genuine,
confirmed presence in StaffLine rather than the API's immediate response;
and a later `push` against the same batch and the same tenant leaves that
result unchanged and creates nothing new.

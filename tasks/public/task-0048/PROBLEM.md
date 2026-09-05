# Migrate the Brackett closure archive onto Paygrade

**From:** Integrations / Workforce Systems, Northgate Facilities Group
**Vendor:** Paygrade
**Surface:** polling · writeback
**Category:** migrate · **Track:** python · **Tier:** 3

## Context

Brackett HR is being retired at the end of the month. Its final closure
archive is the last record of workers, placements, and pay periods that the
branch closed. The overnight bridge will disappear with Brackett, leaving
Paygrade as the system of record. The archive layout and the customer’s
cutover rules are in `docs/brackett-paygrade-cutover-note.md`.

We need one register the operations desk can use on Monday: account for every
archive row, apply the agreed worker closure where Paygrade still permits it,
and identify anything the desk must resolve manually. Paygrade’s vendor
documentation is in `docs/` — start at `docs/index.md`.

## What we need

The test harness runs your code exactly as follows — this command is the
contract:

```bash
python -m pg_closure_migrate
# -> writes the output artifacts listed below
```

Read `input/brackett_closure_archive.csv` and give every row exactly one of:

- **`already_closed`** — Paygrade has closed the record and the register carries its removal instant.
- **`closed_here`** — Paygrade still holds a worker with no running placement; close that worker there.
- **`blocked`** — Paygrade still holds a worker with a running placement; name the placements holding it open.
- **`no_close_surface`** — Paygrade still holds a non-worker for which this task has no write surface.
- **`not_in_paygrade`** — Paygrade has neither the record nor a published removal for that id.

A worker closed by this pass must have employment status `terminated`. The
register must also include Paygrade removals absent from the archive under
`discovered`. `WORKER`, `PLACEMENT`, and `PERIOD` map to Paygrade employee,
assignment, and payrun respectively.

## Output artifacts

- `output/result.json` — `{"rows": [...], "counts": {...}, "discovered": [...]}`.
  Each row carries `ref`, `record_kind`, `pg_id`, `outcome`, `removed_at`, and
  `blocked_by`; discovered entries carry `entity`, `id`, and `deleted_at`.
- `output/import_report.csv` — header
  `brackett_ref,record_kind,pg_id,outcome,removed_at,blocked_by`, followed by
  one row per archive row. Use an empty field where a value does not apply and
  join placement ids in `blocked_by` with `;`.
- `output/writeback_log.json` — one entry per worker this pass closes:
  `{"brackett_ref", "pg_id", "status"}`.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Vendor sandbox base URL (for example, `http://paygrade:8000`) |
| `OUTPUT_DIR` | Directory where output artifacts land (defaults to `./output`) |
| `PG_APP_TOKEN` | Vendor credential injected by the test harness |

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

The entry command exits 0, every archive row has the right outcome, every
permitted worker closure is applied, no other worker is changed, blocked rows
identify their holders, and the register accounts for Paygrade removals absent
from the archive.

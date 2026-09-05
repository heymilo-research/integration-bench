# Build the nightly payroll bridge for the punch export

**From:** Payroll Systems / Harbor Point Facilities
**Vendor:** Rosterly
**Surface:** polling, writeback
**Category:** build · **Tier:** 3

## Context

Our overnight rota lives in Rosterly. Every morning the timekeeping partner
drops a file of clock-in/clock-out pairs keyed by Rosterly shift.

Payroll pays by the **day**, so a punch crossing midnight must be split and
each day given its minutes. The repository contains the initial bridge.

Payroll Systems' own runbook for the job is at
`docs/harborpoint-payroll-runbook.md`. Full vendor documentation is in `docs/` —
start at `docs/index.md`.

### The rules payroll work to

These are ours, not Rosterly's.

- **A payroll day is a calendar day at the shift's venue.** Split a punch at
  venue midnight; its daily minutes must add up to its total length.
- **The partner corrects punches by re-sending them.** A correction carries the
  same `punch_ref`. The last row the file gives for a `punch_ref` is the punch;
  earlier rows for that ref are what the clock held before a supervisor fixed
  it, and they are not additional work.
- **A punch we cannot place is an exception, not a guess.** If Rosterly has no
  such shift, the reason is `unknown_shift`; if Rosterly has it but no longer
  holds it against the rota, the reason is `deleted_shift`. Exceptions stay off
  the payroll report entirely and nothing is written back for them.
- **Arrival** is the clock-on measured against the shift's scheduled start:
  `early` at 5 minutes or more before it, `late` at 5 minutes or more after it,
  `on_time` in between.
- **Scheduling get told about split punches.** Each gets one note against the
  crew member, using the package's established key and format and carrying the
  punch, shift, venue timezone, and daily minutes. The bridge is routinely
  re-run over the same export.

## What we need

The harness runs your code exactly as follows — this command is the contract:

```bash
python -m harborpoint_bridge
# -> writes the output artifacts listed below
```

The export is at `input/punches.csv`: `punch_ref`, `shift_id`, `punch_in_utc`,
`punch_out_utc`.

## Output artifacts

- `output/import_report.csv` — header row plus one row per payroll day of every
  payable punch: `punch_ref`, `shift_id`, `worker_id`, `venue_timezone`,
  `payroll_date` (`YYYY-MM-DD`), `minutes`.
- `output/result.json` — `punch_count`, `bridged_count`, `unbridgeable_count`,
  `split_line_count`, `midnight_split_count`, `total_minutes`, `notes_posted`,
  plus `punches` (one entry per payable punch: `punch_ref`, `shift_id`,
  `worker_id`, `venue_timezone`, `arrival`, `minutes`, and `days`, a list of
  `payroll_date`/`minutes`) and `unbridgeable` (`punch_ref`, `shift_id`,
  `reason`).
- `output/writeback_log.json` — `notes`: one entry per note the run wrote back,
  each with `punch_ref`, `shift_id`, `worker_id`, `idempotency_key`, `note_id`.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Vendor sandbox base URL (e.g. `http://vendor:8000`) |
| `RY_CLIENT_ID` | Vendor credential injected by the test harness |
| `RY_CLIENT_SECRET` | Vendor credential injected by the test harness |
| `INPUT_FILE` | Path to the partner's punch export |
| `OUTPUT_DIR` | Directory where output artifacts land (defaults to `./output`) |
| `PAGE_LIMIT` | Optional batch-size override |

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

The entry command exits 0, every payable punch's minutes sit on the payroll days
the venue worked them, every punch we cannot place is on the exception list with
a reason and nowhere else, scheduling holds exactly one note per split punch,
and running the bridge again over the same export changes neither the artifacts
nor what Rosterly holds.

# Move the rota mirror onto instant storage

**From:** Data Platform — Nordhavn Care Group
**Vendor:** Rosterly
**Surface:** polling
**Category:** migrate · **Track:** python · **Tier:** 2

## Context

We keep a Postgres mirror of the rota rows that belong to our own crew on a
shared Rosterly tenant. It has held them as two columns of text since it went
in: the wall clock Rosterly printed, and the name of the clock that wall clock
is on.

Group Reporting are building one capacity picture across every venue we staff
and they cannot do it on text. Two stamps written on two different clocks do
not sort, do not subtract and do not join. So the mirror moves onto instants.

The nightly refresh job in this repo is what feeds the mirror today. Your job is
to make it emit the new shape instead of the old one. There is a note from when
this was costed in `docs/nordhavn-mirror-migration-spec.md`.

## What is in scope

`input/crew_roster.csv` is the crew we own on that tenant. In scope is every
worker on it, plus every shift and every interview Rosterly holds that names one
of those workers. Nothing else on the tenant is ours.

`input/mirror_inventory.csv` is a dump of what the mirror holds right now, in
the old shape. Anything in scope that is not in it is new to us.

## The new row shape

One row per in-scope record:

| Column | Meaning |
|---|---|
| `entity` | `worker`, `shift` or `interview` |
| `record_id` | Rosterly's id for the record |
| `zone` | the IANA clock name this record's stamps are expressed in |
| `local_wall_clock` | the record's `updated_at` wall clock, no zone suffix |
| `updated_utc` | the same moment as a UTC instant, `YYYY-MM-DDTHH:MM:SSZ` |
| `utc_offset` | the difference between the two above it, `+HH:MM` / `-HH:MM` |
| `state` | `active`, or `retired` if the tenant has retired the record |

## Run contract

The test harness runs your code exactly as follows — this command is the
contract:

```bash
python -m nordhavn_mirror_port
```

## Output artifacts

- `output/import_report.csv` — header row plus one row per in-scope record, in
  the column order of the table above.
- `output/result.json` — an object with `migrated_row_count`,
  `active_row_count`, `retired_row_count`, `adopted_row_count`, `adopted`
  (the ids in scope that the mirror inventory did not already hold, sorted),
  and `zone_offsets` (an object mapping each clock name that appears in the
  report to the offset the run recorded for it).

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Vendor sandbox base URL |
| `RY_CLIENT_ID` | Vendor credential injected by the test harness |
| `RY_CLIENT_SECRET` | Vendor credential injected by the test harness |
| `CREW_ROSTER_FILE` | Path to the crew roster |
| `MIRROR_INVENTORY_FILE` | Path to the mirror inventory dump |
| `OUTPUT_DIR` | Directory where output artifacts land |

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

The entry command exits 0, every in-scope record appears exactly once in the
report carrying the instant it really denotes, and the summary's counts agree
with the rows underneath them.

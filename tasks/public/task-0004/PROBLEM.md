# Merge StaffLine application stage with Placemint placement stage

**From:** RevOps / Systems Integration
**Vendors:** StaffLine (recruiting pipeline, system of record for candidate identity) + Placemint (commercial placement outcome, system of record for placement status)
**Surfaces:** polling (pull), writeback (push)

## Context

We run StaffLine for recruiting ops and Placemint for the commercial
placement side of the same book of business. The two systems currently
disagree about where several candidates actually stand: StaffLine still
shows some candidates mid-pipeline while Placemint's placement records show
the deal already closed or fallen through, and vice versa.

Ops wants one merged roster, with a simple rule for who's right when the two
disagree: **once a Placemint placement exists for a candidate, its stage is
the one that counts** -- StaffLine's own stage on that candidate's
application is considered stale and must be overridden in the merged output.
Every candidate we override this way also needs a note appended on their
StaffLine record, so recruiters stop working candidates who are already
placed elsewhere in the pipeline.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## What we need

The grader drives two one-shot subcommands, in this order:

```bash
python -m staffline_placemint_merge merge

python -m staffline_placemint_merge correct
```

## Output format

`roster.json` is a JSON array of rows sorted by `source_id` (StaffLine's own
application id): `{"source_id", "candidate_id", "candidate_name",
"join_key", "job_id", "job_title", "staffline_stage", "stage",
"source_of_truth", "placemint_placement_id"}`. `corrections.json` is a JSON
array sorted by `candidate_id`: `{"candidate_id", "application_id",
"target_stage", "note_text", "ok", "note_id", "err"}`. Do not change these
shapes.

## Environment

| Variable | Meaning |
|---|---|
| `STAFFLINE_BASE_URL` | StaffLine sandbox base URL |
| `SL_APP_TOKEN` / `SL_HMAC_SECRET` | StaffLine's static-token + HMAC-signing auth |
| `PLACEMINT_BASE_URL` | Placemint sandbox base URL |
| `PM_CLIENT_ID` / `PM_CLIENT_SECRET` | Placemint OAuth client-credentials |
| `OUTPUT_DIR` | where output files go (defaults to `./output`) |

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

`merge`'s output matches the tenant's actual upstream state on both
vendors; `correct`'s writes land on StaffLine and genuinely succeed; and a
rerun of `correct` never repeats a correction it already recorded.

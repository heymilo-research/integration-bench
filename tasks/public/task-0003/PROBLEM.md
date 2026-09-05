# Build the weekly Placemint contact sweep

**From:** Integrations / Delivery Ops
**Vendor:** Placemint
**Surface:** polling, writeback
**Category:** build · **Track:** python · **Tier:** 2

## Context

Meridian Talent Group runs its whole delivery pipeline in Placemint. Every open
placement is supposed to get a touch from an account manager at least once a
fortnight, and the way anyone knows a touch happened is that somebody filed a
note against the placement in Placemint.

Right now that check is a person. Every Monday Priya exports the pipeline into a
spreadsheet, eyeballs it against the notes, and pings the desk about whatever
has gone quiet. She is going on secondment in three weeks and nobody wants to
inherit the spreadsheet, so this is the job: a sweep that reads Placemint,
works out when each open placement was last touched, and files the chase-up
notes itself so the account manager sees them in their Placemint inbox.

Delivery Ops keep their own runbook for the manual version at
`docs/meridian-contact-sweep-runbook.md`. Full vendor documentation is in
`docs/` — start at `docs/index.md`.

### The rules Delivery Ops settled on

These are ours, not Placemint's.

- **Scope.** The sweep covers the placements Placemint currently holds that are
  still open: not retired, and in one of the stages `sourced`, `submitted`,
  `interviewing`, `offered`. A placement outside that scope is not reported and
  is never written to.
- **Last contact** for a placement is the `created_at` of the most recent note
  Placemint holds against it. A placement Placemint holds no note for has no
  last contact.
- **Stalled** means the last contact is earlier than `STALE_BEFORE`, or there
  has never been any contact.
- Every stalled placement gets exactly **one** note, authored
  `sweep@meridian.test`, whose body is
  `Chase-up: no contact since <last contact>.` — with `<last contact>` the
  timestamp exactly as Placemint gave it — or, where there has never been any
  contact, `Chase-up: no contact on record.`
- Nothing is written for a placement that is not stalled.
- Running the sweep a second time must not file a second note anywhere.

## What we need

The harness runs your code exactly as follows — this command is the contract:

```bash
python -m placemint_contact_sweep sweep
# -> writes the output artifact listed below
```

1. Read the pipeline and the notes out of Placemint.
2. Decide, per placement in scope, when it was last contacted and whether that
   makes it stalled.
3. File the chase-up notes and write the report.

## Output artifacts

- `output/sweep_report.json` — `scope_count`, `stalled_count`, `fresh_count`,
  and `placements`: one entry per placement in scope, each with its
  `placement_id`, `client_id`, `stage`, the `last_note_id` and
  `last_contact_at` of its last contact (both `null` where there is none),
  `stalled`, and the `note_id` Placemint gave the chase-up note (`null` where
  none was filed).

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Vendor sandbox base URL (e.g. `http://vendor:8000`) |
| `PM_CLIENT_ID` | Vendor credential injected by the test harness |
| `PM_CLIENT_SECRET` | Vendor credential injected by the test harness |
| `STALE_BEFORE` | The review horizon, an ISO 8601 Z instant |
| `OUTPUT_DIR` | Directory where output artifacts land (defaults to `./output`) |

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

The entry command exits 0, every open placement is in the report with the
contact Placemint actually holds against it, every stalled one carries exactly
one chase-up note saying the right thing, nothing outside the scope has been
written to, and a second run files nothing further.

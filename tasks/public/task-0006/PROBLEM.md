# The case-note mirror is short every morning

**From:** Clinical Systems / Ellerby Health
**Vendor:** Rosterly
**Surface:** polling
**Category:** harden · **Tier:** 3

## Context

Our safeguarding team works from a nightly mirror of the case notes attached to
carers in Rosterly. The job in `repo/` came across from the pilot tenant and has
been running successfully in production: it exits cleanly, writes both files,
and gives the morning meeting a plausible-looking total.

The live tenant is now much larger than the pilot. During an audit, Clinical
Governance sampled notes visible in Rosterly and found that a substantial number
never reached `import_report.csv`. The missing notes do not fall into one date
range, author, or carer status, and the job has not raised an operational error.
We need the existing connector hardened so that a successful run means the
mirror is genuinely complete.

Ellerby's operating note for this job is at
`docs/ellerby-case-note-mirror-note.md`. Full vendor documentation is in
`docs/` — start at `docs/index.md`.

## What we need

The harness runs one nightly pass exactly as follows:

```bash
python -m ellerby_note_mirror
```

Keep the existing package and artifact shapes. A pass must read the complete
Rosterly roster and mirror every case note the tenant currently holds, exactly
once. A note remains part of the mirror for as long as Rosterly returns it,
including notes and carers represented as retired upstream. The job must not
report a partial pass as complete.

## Output artifacts

- `output/import_report.csv` — header row plus one row per case note, sorted by
  `note_id`: `note_id`, `worker_id`, `author`, `body`, `created_utc`,
  `updated_utc`, `state`. Both timestamps are UTC strings in
  `YYYY-MM-DDTHH:MM:SSZ` form. `state` is `active` or `retired`.
- `output/result.json` — `workers_on_roster`, `workers_polled`, `note_count`,
  `active_note_count`, and `retired_note_count` for that same pass.

Keep transport, mirror decisions, and artifact writing separate.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Rosterly sandbox base URL |
| `RY_CLIENT_ID` | OAuth client id injected by the harness |
| `RY_CLIENT_SECRET` | OAuth client secret injected by the harness |
| `OUTPUT_DIR` | Artifact directory (defaults to `./output`) |
| `PAGE_LIMIT` | Optional page-size override |

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

`python -m ellerby_note_mirror` exits 0, the report contains exactly one
correct row for every case note Rosterly holds against the complete roster,
the summary counts agree with that report, retired material is represented
with the required state, and no incomplete pass is presented as a successful
mirror.

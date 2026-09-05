# Get the scorecard export into TalentLoop

**From:** Integrations / Customer Success Engineering
**Vendor:** TalentLoop
**Surface:** writeback
**Category:** build · **Tier:** 2

## Context

A tenant is retiring the shared drive their recruiting coordinators have been
dumping interview scorecards into for the last four years. Everything in it has
been exported, matched up to a TalentLoop candidate, and handed to us as one
JSON file. They want the documents living on the candidate records in TalentLoop
so their coordinators stop having two places to look.

The export is at `input/scorecards.json` (`INPUT_FILE`). Each row is one
document: the candidate it belongs to, the file's name and content type, a
SHA-256 digest their export tool recorded, and the file itself inline as base64.

The tenant is not expecting a clean sweep — four years of a shared drive is
four years of a shared drive. What they cannot accept is a report that disagrees
with the records themselves, because their compliance team samples both. So the
report matters as much as the upload: every document ends up in exactly one of
three buckets, and the bucket has to be true.

- **delivered** — the file is on the candidate's record in TalentLoop.
- **quarantined** — we got as far as the candidate but the document itself did
  not make it. Say why, using the platform's own wording for the problem.
- **unresolved** — there was no candidate to write to. Distinguish a candidate
  TalentLoop used to have and no longer does from an id TalentLoop never issued
  at all: the first is a data-retention question for the tenant's legal team,
  the second is a bug in their export tooling, and they route to different
  people. Their triage tooling keys off the row's `reason`, so use their two
  agreed labels: `candidate_deleted` for the first, `candidate_missing` for the
  second.

One business rule they were explicit about: if the only thing wrong with a
document is a piece of metadata we can derive from the file we are holding, fix
it and send it again once. If the platform will not take the document itself,
that is a quarantine.

Notes should read the way their coordinators write them: `<doc_ref>: <summary>`,
authored by the person named on the row.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## What we need

The test harness runs your code exactly as follows — this command is the
contract:

```bash
python -m talentloop_scorecards deliver
# -> writes the output artifacts listed below
```

## Output artifacts

- `output/attachment_report.json` — `document_count`, `delivered_count`,
  `quarantined_count`, `unresolved_count`, and `documents`: one row per export
  row with `doc_ref`, `candidate_id`, `outcome`, `reason`, `note_id`,
  `attachment_id`, `attachment_state`, `attachment_reason`, `sha256`.
- `output/quarantine.json` — `count` plus `documents`, the subset of rows whose
  outcome is not `delivered`, each with its `doc_ref`, `candidate_id`,
  `outcome`, `reason` and `note_id`.

Row order in either file is yours to choose.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Vendor sandbox base URL (e.g. `http://vendor:8000`) |
| `TL_CLIENT_ID` | Vendor credential injected by the test harness |
| `TL_CLIENT_SECRET` | Vendor credential injected by the test harness |
| `INPUT_FILE` | Path to the scorecard export |
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

The entry command exits 0, both artifacts are present and well-formed, every
export row carries the outcome that matches the tenant's actual state in
TalentLoop, and running it a second time against the same export leaves that
state and that report unchanged.

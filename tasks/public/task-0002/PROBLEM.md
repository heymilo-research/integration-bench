# Legal-hold export: TalentForge custodians and everything on file against them

**From:** Integrations / Customer Success Engineering
**Vendor:** TalentForge (enterprise ATS/CRM)
**Surface:** polling (pull)
**Category:** build · **Track:** python · **Tier:** 2

## Context

A staffing customer of ours is in litigation and their counsel has issued a
preservation notice. It names a set of people whose TalentForge records — and
every recruiter note filed against them — have to be collected into one
reviewable file and handed to the other side's e-discovery vendor. Counsel will
re-request this at intervals while the matter runs, so it has to be a job we can
re-run against a tenant that has moved on, not something an account manager
assembles by hand in the TalentForge UI once.

The preservation list is in the repo at `input/legal_hold_roster.csv`
(`matter_ref,custodian_email`). Email addresses are all we get: counsel has no
TalentForge ids and is never going to have any.

A handover note from the first manual production is at
`input/HANDOVER-legal-hold.md`.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## Scope rules

These come from our legal team. They are not derivable from anything in
TalentForge, so take them as given:

- **A hold attaches to an address, not to a record.** If more than one person in
  the tenant carries a roster address, every one of them is a custodian. We do
  not get to decide which one counsel meant.
- **Scope is the roster, not the state of the file.** Archived, closed and
  withdrawn people are in scope exactly like active ones, and their notes come
  with them. A hold does not lapse because someone's file did.
- **Completeness is the deliverable.** Review proceeds on the assumption that
  the export is everything the tenant holds against each custodian.
- **An address we cannot place is a finding.** Report it; never drop it.

## What we need

The grader runs your package exactly this way — this command is the contract:

```bash
python -m talentforge_legal_hold export
```

It writes one artifact, `$OUTPUT_DIR/legal_hold_export.json`:

| Key | Meaning |
|---|---|
| `roster_row_count` | rows read from the roster CSV |
| `custodian_count` | custodians in this export |
| `note_count` | notes across all custodians |
| `custodians[]` | one object per custodian (below) |
| `unmatched_roster_emails[]` | roster addresses that match nobody in the tenant |

Each custodian object:

| Key | Meaning |
|---|---|
| `matter_ref` / `roster_email` | the roster row this custodian came from |
| `candidate_id` | the TalentForge record id |
| `given_name`, `family_name`, `phone`, `pipeline_status`, `is_deleted` | as the tenant holds them |
| `created_at`, `updated_at` | UTC ISO 8601 seconds (`2020-01-02T03:04:05Z`) |
| `notes[]` | `note_id`, `author`, `body`, `created_at` — the note's own timestamp, carried across unchanged |

A custodian with nothing on file still gets a row, with `"notes": []` and the
rest of their details filled in — counsel asked to be told about those people
too. Ordering inside the file is already handled by the starter's writer, so
build the lists in whatever order suits you.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | TalentForge sandbox base URL (e.g. `http://vendor:8000`) |
| `TF_CLIENT_ID` / `TF_CLIENT_SECRET` | this tenant's OAuth client credentials |
| `ROSTER_PATH` | the preservation roster CSV |
| `OUTPUT_DIR` | where `legal_hold_export.json` lands (defaults to `./output`) |

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

`python -m talentforge_legal_hold export` exits 0 and `legal_hold_export.json`
describes exactly the people the roster names, as TalentForge holds them at the
moment it runs, with everything on file against each of them and nothing that
belongs to anybody else.

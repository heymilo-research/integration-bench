# Get the Riverside signup file onto the CrewCall roster

**From:** Integrations / Workforce Ops
**Vendor:** CrewCall
**Surface:** polling, writeback
**Category:** build · **Tier:** 2

## Context

Riverside Staffing covers our overflow shifts. Their portal exports everyone who
has put their name down since the last export; we get those people onto the
tenant's CrewCall roster so dispatch can book them. The file is at
`input/signups.csv` and it is exactly what their portal produces — we do not get
to ask them to change it. It is one row per submission.

Workforce Ops keep their own runbook for this job at
`docs/riverside-signup-runbook.md`.

### The rules Workforce Ops settled on

These are ours, not CrewCall's.

- **Two submissions are the same person when the last seven digits of their
  phone numbers match**, once punctuation, spacing and any country code are
  ignored.
- **Within one person's submissions, the newest wins**: the highest
  `submitted_at`, and where two are identical, the lower `submission_id`. That
  submission is the person's canonical record.
- **A blank field on the canonical submission is filled from the next-newest
  submission that has a value for it.**
- The name comes out with the whitespace tidied and each part capitalised; the
  first word is the given name and everything after it is the family name.
- The email comes out lower-cased, with any `+tag` before the `@` dropped.
- The role comes out lower-cased with spaces written as underscores.
- **Somebody whose CrewCall record has been deleted is not on the roster**, and
  a person who is not on the roster is a new hire.

And the standing rule for every import we run: **this creates people, it never
edits them.** Where CrewCall already holds someone, their CrewCall record is the
truth and a signup form has nothing to say about it.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## What we need

The harness runs your code exactly as follows — this command is the contract:

```bash
python -m crewcall_signup_import import-signups
# -> writes the output artifact listed below
```

For each person the file describes, once their submissions have been collapsed
into one:

1. Work out whether CrewCall already holds that person.
2. If it does not, sign them up with their canonical name, email, phone and role.
3. If it does, leave their CrewCall record alone and record them as skipped.

Then write the report described below. Running the command a second time over
the same file must not sign anybody up twice and must not touch anybody.

## Output artifacts

- `output/dedupe_report.json` — `row_count`, `person_count`, `created_count`,
  `skipped_count`, and `people`: one entry per person, each with the
  `person_key` (the identity value above), the `survivor_submission_id`, the
  full list of `submission_ids` that collapsed into that person, the canonical
  `first_name`, `last_name`, `email` and `role`, the `outcome` (`created` or
  `skipped`), and the `worker_id` that person corresponds to in CrewCall.

Ordering within the file is not significant.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Vendor sandbox base URL (e.g. `http://vendor:8000`) |
| `CC_API_KEY` | Vendor credential injected by the test harness |
| `INPUT_FILE` | Path to the agency's signup export |
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

The entry command exits 0, the report accounts for every submission under the
right person with the right canonical values, the roster ends up holding exactly
the people it held before plus the ones the file genuinely added — one record
each — nobody who was already there has been modified, and a second run changes
nothing.

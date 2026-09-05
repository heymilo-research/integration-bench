# Harden the Placemint book extract for the larger tenant

**From:** Integrations / Revenue Ops
**Vendor:** Placemint
**Surface:** polling
**Category:** harden · **Tier:** 3

## Context

Every night this connector pulls Meridian Talent Group's placement book out of
Placemint and writes the extract Finance bill from: one row per placement, with
the account it sits under and whether that account is still trading with us. It
has run against our own tenant for a year and nobody has touched it.

We are moving it onto the bigger tenant next week, and the pilot run has come
back badly. Dana in Finance:

> "I have raised invoices against two accounts that were shut down before
> Christmas, and one that we stopped working in January is on here for four
> placements. There are account names in this file that those clients stopped
> using when they rebranded — my AP contact did not recognise them. I cannot
> send this out."

Revenue Ops keep their own note on this job at
`docs/meridian-account-book-note.md`. Full vendor documentation is in `docs/` —
start at `docs/index.md`.

### The rules, unchanged

- One row per placement on the book. A placement Placemint has retired is not on
  the book and is not in the extract.
- `billable` is Revenue Ops' rule and has not changed: the account the placement
  sits under is still trading with Meridian. Anything else is on hold.
- The account name and industry on a row are that account's current ones.
- `fee_total_billable` is the sum of the fees on the billable rows; a placement
  carrying no fee still gets a row.

## What we need

The harness runs your code exactly as follows — this command is the contract:

```bash
python -m placemint_book_extract extract
# -> writes the output artifact listed below
```

1. Work out why the account side of the extract is wrong.
2. Fix it, keeping the rules above.
3. Re-run; two runs over an unchanged tenant produce the same extract.

## Output artifacts

- `output/book_extract.json` — `placement_count`, `billable_count`,
  `on_hold_count`, `fee_total_billable`, and `placements`: one entry per
  placement on the book, sorted by `placement_id`, each with its
  `placement_id`, `client_id`, `client_name`, `client_industry`,
  `candidate_name`, `role_title`, `stage`, `fee_amount` (null when it carries
  none) and `billable`.

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Vendor sandbox base URL (e.g. `http://vendor:8000`) |
| `PM_CLIENT_ID` | Vendor credential injected by the test harness |
| `PM_CLIENT_SECRET` | Vendor credential injected by the test harness |
| `ACCOUNT_BOOK_FILE` | Path to Meridian's weekly account snapshot |
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

The entry command exits 0, the extract covers every placement on the book and
nothing else, every row carries the account Placemint holds it under and the
right billing verdict, the totals agree with the rows, and a second run produces
the same extract.

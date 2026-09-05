# GlobalHire — audit-period extracts (runbook)

**Meridian Talent Systems / Data Platform. This is our own runbook, not
GlobalHire documentation.** Written when we built the first audit extract for
Compliance. Last revised in January.

Compliance asks the same question every quarter: for each period they are
reviewing, what did GlobalHire touch? This note is how we answer it without
dragging the whole tenant through the pipeline every time.

## The calendar

Compliance hands us the periods in `input/ledger_windows.csv`. Do not reorder
it and do not merge periods — the audit pack is read side by side with their own
calendar, and the periods are whatever they sampled. They are not contiguous and
they are not the same length; some quarters they hand us four periods, some
quarters twenty.

Each row is half-open: `starts_at` counts, `ends_at` does not.

## Chunking the extract

GlobalHire's list endpoints take a pair of incremental parameters — the
`modified_since` you will find in their developer guide, and `modified_until`,
which is its upper bound. Both are UTC ISO-8601; `modified_since` is inclusive
and `modified_until` is exclusive, so a period maps onto them one to one:

```
GET /v1/candidates?modified_since=<starts_at>&modified_until=<ends_at>
```

Ask for one period at a time and the response holds that period and nothing
else. That is the whole trick: a period is a page or two, so the extract stays
small and Compliance can re-run one period without re-running the quarter. Do
not try to pull the tenant in one go and slice it afterwards — the candidate
collection is thousands of rows and we spent a week on that before switching to
the chunked shape.

Paginate inside a period exactly as the developer guide describes; the
incremental parameters ride along on every page.

## Reading the records

- A record's *activity* is `modified_at`. Records get loaded once and edited
  later, so `created_at` answers a different question and Compliance is not
  asking it.
- Soft deletes stay in the collection with `is_deleted: true`. A delete is an
  activity like any other — the record's last activity is the deletion, and
  Compliance wants it in the period the deletion happened in.
- Records are identified by their GlobalHire id. Ids are stable and are never
  reused, so an id is safe to use as the ledger key.

## Things that have bitten us

- The audit pack is compared against GlobalHire's own UI, so the tenant totals
  in `result.json` have to be what GlobalHire holds today, not what our
  warehouse holds.
- Compliance re-runs the extract when they are challenged on a number. Two runs
  over an unchanged tenant have to produce the same two files, byte for byte.

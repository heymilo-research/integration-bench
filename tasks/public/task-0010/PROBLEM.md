# Build the GlobalHire audit-period extract Compliance keeps asking us for

**From:** Integrations / Data Platform
**Vendor:** GlobalHire
**Surface:** polling
**Category:** build · **Track:** python · **Tier:** 2

## Context

Compliance reviews our GlobalHire tenant a few periods at a time. Every quarter
they hand us a calendar — a list of the periods they are looking at — and ask
what GlobalHire touched inside each one. Until now somebody has answered that
by exporting the tenant to a spreadsheet and filtering it by hand, which took a
week last quarter and disagreed with GlobalHire's own UI twice.

We are replacing that with the job in `repo/`. Its first implementation was
built from the old manual runbook and has not produced a trustworthy audit
pack against the live tenant. Treat the repository as the system to diagnose
and finish, not as a blank scaffold.

This quarter's calendar is in `input/ledger_windows.csv`, in Compliance's
order. Each row is a period, half-open: `starts_at` counts, `ends_at` does not.
The periods are the ones Compliance sampled, so treat the file as given — do
not reorder it and do not merge rows.

Our own runbook for this extract is `docs/ledger-window-runbook.md`. Full
vendor documentation is in `docs/` — start at `docs/index.md`.

## What we need

A record's **activity** is its last modification. A record belongs to the one
period that contains it, and to no period at all if it falls outside every row
of the calendar. Each record's **outcome** is `deleted` if GlobalHire has the
record flagged deleted, `created` if its created and modified stamps are the
same instant, and `updated` otherwise.

The harness runs your code exactly as follows — this command is the contract:

```bash
python -m gh_activity_ledger
# -> writes both artifacts below into OUTPUT_DIR
```

### `activity_ledger.csv`

A header row plus one row per record placed in a period, ordered by period in
calendar order, then entity, then `record_id`:

```
window_id,entity,record_id,outcome
W0,candidates,XX_00000,created
```

### `result.json`

The summary the audit pack embeds. `per_window` is one entry per period in
calendar order; `tenant` is how many records GlobalHire holds; `outside_windows`
is how many of those the ledger placed in no period:

```json
{"per_window": [{"window_id": "W0",
                 "candidates": {"total": 0, "live": 0, "deleted": 0},
                 "placements": {"total": 0, "live": 0, "deleted": 0},
                 "agencies":   {"total": 0, "live": 0, "deleted": 0}}],
 "tenant": {"candidates": 0, "placements": 0, "agencies": 0},
 "outside_windows": {"candidates": 0, "placements": 0, "agencies": 0}}
```

## Environment

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | GlobalHire sandbox base URL |
| `GH_API_KEY` | vendor credential injected by the harness |
| `OUTPUT_DIR` | directory the two artifacts are written to (defaults to `./output`) |

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

The entry command exits 0, both artifacts are present and in the shapes above,
every record GlobalHire holds appears under the one period its last activity
falls in — and under no period when it falls outside the calendar — the
summary's counts and its live/deleted split agree with the ledger and with the
tenant, and running the extract twice over an unchanged tenant produces the same
two files both times.

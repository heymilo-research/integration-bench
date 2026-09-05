# Rebuild the Vettly change feed for the vaultd cutover

vaultd is our OAuth broker. Platform is switching it off at the end of the
month, and the Vettly change feed is the last one still using it.

The transport half of the move is already done: the connector mints and holds
its own Vettly grant now instead of asking vaultd for a bearer. The change-feed
cycle has not yet been proven against the direct vendor path. Inspect the
repository as a whole and complete the cutover behavior described below.

Vettly is our background-screening platform: a **subject** is a person, a
**check** is a piece of screening we ordered on them, and a **report** is the
result document a check produces.

## What the cycle has to do

The warehouse's nightly window is twenty minutes and its load is sized for a
delta, so the cycle reports what Vettly has moved and nothing else. vaultd's
handover copy for this feed is in `input/vaultd_state.json`:

```json
{"broker": "vaultd", "tenant": "...", "feed": "...",
 "last_delivered_cursor": "...", "cycles_delivered": 0}
```

`last_delivered_cursor` is the point the warehouse has already been carried
through. Everything the tenant has moved from that point onward is this
cycle's; anything before it has been delivered and must not come round again.

Every reported record gets one entry, and the entry says three things the
warehouse cannot work out for itself:

- **`op`** is `retire` where Vettly no longer holds the record as a live one,
  and `upsert` otherwise. Decide it from that record's own state at Vettly and
  from nothing else: one record's operation never follows from another's.
- **`subject_id` / `subject_email`** are the person the record belongs to. The
  warehouse is keyed by person: a subject is its own person, a check names the
  person it was ordered on, and a report belongs to the person its check was
  ordered on.
- **`detail`** is the field that moved for that kind of record: a subject's
  `email`, a check's `status`, a report's `result`.

## Outputs

`output/result.json`:

```json
{"cursor_used": 0, "next_cursor": 0, "record_count": 0,
 "counts": {"subject": 0, "check": 0, "report": 0, "upsert": 0, "retire": 0},
 "retired_ids": [],
 "changes": [{"record_id": "<id>", "kind": "subject", "op": "upsert",
              "subject_id": "<id>", "subject_email": "someone@example.invalid",
              "updated_at": 0, "detail": "<value>"}]}
```

`kind` is `subject`, `check` or `report`. `updated_at` is Vettly's own
last-modified stamp for the record, carried through unchanged. `retired_ids`
is the purge list the warehouse runs after the load: the ids of the entries
whose `op` is `retire`. `cursor_used` is the watermark this cycle asked Vettly
for and `next_cursor` is the one the next cycle should ask for — both written
the way Vettly expects a watermark, because the next cycle reads them straight
back out of this file.

`output/import_report.csv` — the loader's instruction file. Header plus one row
per entry in `changes`, nothing else:

```
record_id,kind,op,subject_id,subject_email
```

Order is not significant in either file.

## Running it

The grader runs `python -m vaultd_cutover sync`.

| Variable | Meaning |
|---|---|
| `VENDOR_BASE_URL` | Vettly base URL |
| `VT_CLIENT_ID`, `VT_CLIENT_SECRET` | Vettly credentials, ours now rather than vaultd's |
| `INPUT_DIR` | where vaultd's handover state file is dropped |
| `OUTPUT_DIR` | where the two artifacts are written |

Full vendor documentation is in `docs/` — start at `docs/index.md`. The
handover note the outgoing engineer left is in there too.

## Done when

`python -m vaultd_cutover sync` exits 0, the change file carries exactly the
records Vettly has moved since the handover point with the operation and the
person each one earns, and the next cycle can pick up from `next_cursor` with
no gap and no repeat.

## Engineering expectations

This ships to production, so the usual bar applies:

- Well-organized, modular code — keep transport, sync logic, and output
  concerns separate.
- Tests, runnable with `pytest`, covering the core logic you add or change.
- Be a good API citizen: handle the vendor's real-world behavior the way a
  production integration must, and don't hammer endpoints in retry loops.
  Our platform team audits vendor request logs — sloppy conduct gets
  flagged even when the data lands correctly.

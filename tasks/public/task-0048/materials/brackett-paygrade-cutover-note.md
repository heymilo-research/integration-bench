# Brackett → Paygrade cutover note

> **Source document.** Integration note issued by Brackett HR Systems Ltd,
> "Closure Archive — Paygrade Cutover", revision 2025-08. Reproduced here as
> received. This is Brackett's document, not Paygrade's.

Brackett is being decommissioned. This note describes the closure archive
Brackett emits on its last day, and how Brackett's own bridge kept it lined up
with Paygrade, so that whoever picks the pipeline up can carry on.

## The closure archive

`brackett_closure_archive.csv`, UTF-8, one header line, one row per record
Brackett closed. RFC 4180 quoting; fields are never multi-line.

| Column | Meaning |
|---|---|
| `seq` | Brackett's emission order, 0-based; unique within the file |
| `brackett_ref` | this closure's Brackett identifier, `BK-nnnn`; unique within the file |
| `record_kind` | which kind of Paygrade record this closure is about: `WORKER`, `PLACEMENT` or `PERIOD` |
| `pg_id` | the Paygrade id Brackett holds for that record |
| `closed_on` | the date Brackett closed the record |
| `open_placements` | for `WORKER` rows, how many placements the worker still had running when Brackett closed them |
| `close_reason` | Brackett's reason code, free text |

Brackett never mints Paygrade ids of its own, and a column that does not apply
to a row is emitted as `0` or empty rather than as a `NULL` sentinel.

## How Brackett read Paygrade

Brackett's bridge mirrored the tenant every night over Paygrade's RPC surface.
The four read methods it used were `listEmployees`, `listAssignments`,
`listPayruns` and `listTombstones`; single records were fetched with
`getEmployee`, `getAssignment` and `getPayrun`. All of them page with the
`start`/`count` envelope Paygrade documents.

Paygrade's own guide covers employees and assignments. Pay periods are on the
same surface and behave the same way — `listPayruns` returns `run_nnnn`
records with a `period`, a `status` and a `total_amount`, and `getPayrun`
fetches one by id.

## `open_placements` is reconciled, and it is authoritative

The bridge recomputed every worker's running-placement count from Paygrade on
each nightly pass and wrote the reconciled number back into Brackett before the
closure archive was cut. `open_placements` on an archive row is therefore
Paygrade's own count of the placements that worker still had running, not
Brackett's guess at it, and the closing export is emitted from that same
reconciled snapshot.

There is nothing to be gained by re-deriving that number from
`listAssignments` at cutover time: it is the number the reconciliation already
took from Paygrade, and re-deriving it only risks disagreeing with the archive
the branch signed off.

## Placements close with their worker

Paygrade removes a worker's placements when it removes the worker — the
placement cannot outlive the employment it hangs off, and `employee_id` is a
required reference on every assignment. A `PLACEMENT` row whose worker
Brackett also closed is therefore already gone from Paygrade, and its
`listTombstones` entry arrives alongside the worker's.

So a `PLACEMENT` row can be resolved from the worker's own outcome. Looking
the placement up separately tells you nothing the worker's row has not already
told you.

## Pay periods are immutable

Paygrade never removes a pay period. A period is an accounting object: once
Paygrade has minted it, it stays on the tenant forever, and the delete feed has
no `payrun` entries in it — Paygrade's own guide is explicit that the feed
carries workers and placements. Brackett closed periods in its own ledger for
housekeeping only, which is what the `PERIOD` rows in the archive are.

`PERIOD` rows can therefore be settled from the archive alone. No Paygrade
lookup is required for them.

## Contact

Brackett support ceases with the decommission. Historic archives are held by
the branch for seven years.

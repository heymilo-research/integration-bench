# Placement sync — Revenue Ops runbook

*Northgate Talent Partners / Revenue Operations. This is our own note, not
RecruitOS's documentation and not Placemint's. Last revised in January.*

## Why this job exists

Recruiters work the pipeline in RecruitOS. Billing, the client portal and every
fee the finance team ever sees come out of Placemint. The two have never been
joined up by either vendor, so we join them ourselves: `placement_links.csv` is
the crosswalk Revenue Ops maintain by hand, one line per requisition we bill
for, and this connector is what keeps the two sides from drifting apart.

It runs on a cycle. Each cycle is one invocation. Between cycles the recruiters
carry on working in RecruitOS and the Placemint account managers carry on
working in Placemint, so both sides move and the job has to cope with both.

## The crosswalk

Ken owns `placement_links.csv`. It is edited by hand in a spreadsheet, which
means it is never quite clean:

- Lines survive in it after the requisition has been closed out on the
  Placemint side.
- Lines survive in it after somebody has typed the placement id wrong, or after
  a placement was rebuilt under a new id and nobody told us.

Neither of those is a crisis. They are lines we cannot act on, and the report
is where they get surfaced so Ken can tidy them up in his own time. What we
must never do is treat a line we cannot act on as a failure of the cycle — the
job has to finish and the other lines still have to be right.

## What we learned about Placemint's `updated_at`

This one cost us three weeks in the first year, so it is written down.

`updated_at` on a placement is the **account manager's** clock: it moves when a
Placemint user touches the record in their UI. It does **not** move when we
PATCH the record over the API — the platform treats an API write as a
synchronisation, not as an edit, and deliberately leaves the field alone so that
integrations like ours are not confused by their own traffic.

The practical consequence, and the reason this section exists: **our own writes
never come back to us on the `modified_since` feed.** Anything the feed hands
us on the next cycle is an account manager's move, made in Placemint, by a
human. Treat it as authoritative. There is nothing to filter out and no risk of
a feedback loop, which is why this job can be as simple as it is.

## Retired placements

Placemint's `total` is the count of live placements. When an account manager
retires a placement it drops off the feed and out of `total` on the next crawl —
it is simply not there any more.

So there is no need to re-check the status of every placement on every cycle.
If the crosswalk names a placement and the feed still carries it, it is live.

## Volume

We bill for a few dozen requisitions at a time. Both platforms publish
generous request budgets and we have never come close to either of them. Nobody
has ever needed to think about paging beyond "walk it until `total` says stop".

## Open, not solved

- Ken's spreadsheet has no validation on it at all.
- Nobody reads the report except Ken, and only the counts.
- If the two sides ever disagree about a placement in a way this note does not
  cover, the rule Revenue Ops gave us is in the ticket, not here.

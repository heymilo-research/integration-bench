# Weekly contact sweep — internal runbook

*Meridian Talent Group / Delivery Ops. This is our own note, not Placemint's
documentation. Last touched in October, before the desk reshuffle.*

## What this is

Every Monday morning somebody has to answer the same question: which of our open
placements has nobody spoken to in weeks? Placemint holds the pipeline and the
notes, so the answer is in there; it is just spread across two feeds. The sweep
reads both, works out when each open placement was last touched, and files a
chase-up note on the ones that have gone quiet so the account manager sees it in
their Placemint inbox on the Monday.

## Reading the feed

The placements feed is the easy half — page it and you have the pipeline.

The notes feed is the interesting one. `GET /api/notes` is a single flat feed of
every note in the tenant, each one carrying the `placement_id` it belongs to, so
one crawl gets you the lot; there is no need to go placement by placement.

**The feed comes back oldest first.** Placemint appends a note to the end of the
feed when it is filed, so the rows arrive in the order they were written and the
LAST row a placement appears on is that placement's current note. Build a dict
keyed on `placement_id` while you page and just let each row overwrite the one
before it — when the crawl finishes, whatever is in the dict is the latest note
for every placement, no sorting, no comparisons, nothing to get wrong.

Priya's note from when we built it: "I spent half a day writing a comparator for
this before I realised the feed is already in order. Don't repeat that."

## Cutting the feed down

There is no reason to drag the whole note history across the wire every Monday.
`modified_since` is on every top-level list endpoint (Placemint's
`docs/pagination.md` covers it), so pass the review horizon — the same instant
the sweep is measuring against — and the feed comes back a fraction of the size.
Anything older than the horizon is by definition older than the horizon and
cannot change the answer, so there is no point paying for it.

That took our Monday run from twenty-odd pages to two.

## What we file

One note per stalled placement, authored `sweep@meridian.test`, body naming the
date we last had contact so the account manager does not have to go and look it
up. If we have never had contact at all, say so in as many words rather than
leaving the date blank — Legal were clear that a blank reads as "unknown", and
"we have never spoken to this client about this placement" is not unknown.

## Idempotency

One `Idempotency-Key` per placement (`chase:<placement_id>`). If Monday's run
dies half way through we just run it again.

## Things we have not solved

- Nobody reconciles the sweep against what the account managers actually did. If
  a manager phones a client and does not write it up, we chase a placement that
  was contacted on Friday.
- The sweep does not look at the client record at all, so a client that has gone
  into administration still gets chased.

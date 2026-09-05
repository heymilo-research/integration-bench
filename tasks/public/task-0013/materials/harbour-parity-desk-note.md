# RecruitOS -> warehouse: data platform desk note

*Harbour Talent Group / Data Platform. This is our own note, not RecruitOS's
documentation. Last revised in November.*

## What the nightly pass is

The warehouse keeps one row per RecruitOS candidate, job and application, with
the record's current state and the timestamp RecruitOS gave us for it. Reporting
reads the warehouse; nothing downstream ever talks to RecruitOS directly. If the
warehouse is wrong, the numbers the exec team see are wrong, and nobody finds
out until somebody notices a name they know is in the wrong column.

The pass runs once a night against last night's file and writes a fresh one.

## `synced_through`

Every run stamps the file it writes with the instant it finished. That stamp is
the pass's position, and it is authoritative: it is written by the same code
that wrote the rows, so the two agree by construction. The next run asks
RecruitOS for `modified_since=synced_through` and nothing else.

**A record whose `updated_at` we already hold cannot have changed.** That is the
whole reason the incremental read is safe, and it is why the nightly pass costs
us three requests instead of three hundred. We have never had a reason to read a
collection in full since the original load in the spring.

## The census

The data platform wants a row count with the file. Take `total` straight off the
envelope — RecruitOS's `total` is the count of live records in the collection,
so it is the number the warehouse should be holding once the pass has finished.
It is one line and it is free; do not count the rows yourself.

## Where the ids come from

Every id in the warehouse arrived from a RecruitOS response, so a warehouse row
without a match upstream is not a thing that can happen. We have never needed to
check for one and there is nothing sensible we could do about one if we found
it.

## What the webhook listener is for

RecruitOS pushes an event when something changes. The listener has been up since
March. It is *not* how the warehouse is kept current — the nightly pass does
that — it is how the on-call rota finds out that something moved before the
morning. Record what arrives and leave the reconciling to the pass.

## Open, not solved

- The pass has no retry: if it dies, the next night's run picks up from the
  position on disk.
- Nobody has ever compared the file against RecruitOS end to end. We have
  assumed it agrees since the original load.
- The account team asked for note counts per candidate. RecruitOS only serves
  notes under a candidate, one candidate at a time, so that is 250 requests a
  night and we said no.

# Nightly rota sync — internal runbook

*Marchfield Care Group / Data Platform. This is our own note, not Rosterly's
documentation. Last touched in November, after the incident below.*

## What the pass does

The warehouse mirrors Rosterly. Every night the pass asks each collection what
has changed since the watermark the last pass left behind, appends one row per
change to the change ledger, and moves the watermark on. The loader downstream
reads the ledger and nothing else, so a row that goes in stays in — we have no
way to retract one once the loader has seen it.

The warehouse was loaded from a Rosterly dump when this went in; `SYNC_SINCE` is
the instant of that dump and it is where the first pass starts.

## Why we rewind an hour

Read this before touching `next_watermark`.

The pass hands Rosterly the newest `updated_at` it saw and then **takes an hour
back off it** before storing it. That is deliberate and it is there because of
the night the clocks went back.

On the Sunday of the autumn fall-back the pass ran at its usual time and the
warehouse came out with a day's rota counted twice — the same shifts, the same
interviews, in the ledger twice over, and the capacity report for that Monday
was nonsense as a result. Marta spent the Monday on it and concluded that the
hour that repeats on the fall-back had let the watermark run ahead of changes
that had not landed yet, so the next pass skipped them and a later one picked
them up again out of order. The fix she put in was the hour of margin: start an
hour earlier than you think you need to and nothing can fall down the gap.

It has been in ever since and we have not had the problem again. **Do not take
the rewind out to make a duplicate go away** — the duplicate is the symptom the
rewind exists to prevent, and removing it puts us back where we were last
November.

## Removed records

Rosterly takes a record out of the feed once it is gone — a worker who leaves,
a shift that gets cancelled. If a record comes back to us in `data` then it is
still on the rota, which is why the pass writes every row it receives as an
upsert. We asked about this when the pass went in and were told the collections
only carry what is current.

The loader does have a tombstone path, and the `removed` list in each pass entry
feeds it, but in practice it has always been empty and nobody has exercised it.

## Timestamps

Rosterly's own documentation is right about this and we follow it: the shift and
interview collections give times on the venue's clock with the zone name on the
end, the worker collection gives them in UTC with no suffix, and the ledger's
`updated_at_utc` column is the instant normalised. `parse_wire` does that and it
has never given us trouble.

## Things we have not solved

- The pass has no idea whether the loader has caught up. It writes and moves on.
- Nobody reconciles the ledger against Rosterly afterwards.
- If a pass dies halfway through a collection we re-run it by hand from the
  previous watermark.

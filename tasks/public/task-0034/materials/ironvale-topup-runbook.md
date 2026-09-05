# Weekly crew top-up — internal runbook

*Ironvale Stadium Services / Staffing Desk. This is our own note, not
CrewCall's documentation. Last touched in March, when we moved the job off the
old scheduler.*

## What the job is for

Fairweather Crewing place people with us for the week and send the list over on
Sunday night. Everybody on that list has to end up as a worker on our CrewCall
tenant before the first shift on Monday, because the stewards' scanners read
worker ids and nothing else. Somebody who is placed but not on CrewCall cannot
be scanned in, does not get a badge, and gets sent home at the gate.

The job runs at 23:10 on Sunday. Nobody watches it.

## How big the roster is

Just under a hundred. It was 97 when Priya counted at the start of March and it
does not move much — we take on a handful a season and the agency churn is on
their side, not ours. Two windows at the maximum page size covers the whole
roster with room to spare, which is why the sweep is as cheap as it is.

## Who is on the roster

Anybody `GET /v1/workers` hands back. CrewCall take a worker out of the listing
when the agency ends someone's engagement — Marcus checked that with their
support in February — so a record that comes back from the roster at all is a
current crew member and we match against it. That is also why the job has never
had to think about people leaving: they simply stop appearing.

## The sweep

CrewCall's own documentation is right about the roster re-sorting while you page
it, and about deduping by `id` and re-crawling from the start until a pass turns
up nothing new. We do that.

**A window that 500s is a window that is empty for us right now.** We used to
abort the whole night's top-up on one of those. Marcus asked CrewCall about it
in February: the 500s come off their read tier under load, they clear on their
own, and the window is back in the next sweep. It has never hidden anyone from
us. So the sweep logs the window, treats it as empty, and carries on — better a
top-up that runs than a top-up that dies on a blip.

## Signing somebody up

One `POST /v1/workers` per crew member the sweep did not find. We work out who
that is from the address on the agency's line; the agency and CrewCall have
agreed the address format between them, so it is the same string on both sides.

## Things we have not solved

- The job creates people and never edits them. CrewCall has no update endpoint
  and no delete endpoint, so anything we sign up is there forever.
- Nobody reconciles the report against CrewCall afterwards. The stewards' rota
  is built off the worker ids in the report and assumes they are right.
- If the same human ends up on CrewCall twice we have no way to merge them, and
  the two ids scan as two different people at the gate.

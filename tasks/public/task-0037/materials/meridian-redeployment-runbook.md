# Redeployment sync — internal runbook

*Meridian Talent Group / Delivery Ops. This is our own note, not Placemint's
documentation. Last touched the spring before last, when we wired it up.*

## What this is

A redeployment is one candidate coming off one assignment and going onto
another the same week. Our ATS records it as a single card movement with two
ends — the placement they left and the placement they joined — plus whatever
the consultant typed into the reason box. Every night the ATS drops the day's
movements out as a flat export and this job pushes them into Placemint, because
Placemint is what Finance and the client-facing dashboards read.

## The pair is one movement

Both ends belong together. A leaver closed with no joiner opened is a candidate
who has apparently gone home; a joiner opened with no leaver closed is a
candidate apparently working two assignments at once. Finance reconcile off
these and they will notice.

**Placemint takes the pair as one movement.** The two `PATCH`es and the note go
in under one movement reference, and Placemint settles them together: if the
joiner is refused, the leaver goes back with it and there is nothing for us to
undo. That is why the code writes the leaver first and simply stops the row when
a call comes back refused — there is no unwinding to do, because there is
nothing left half-written.

Dev's note from the build: "I started writing a compensating update for the
leaver and then realised it can never fire. Deleted it."

## Statuses

The ATS has its own vocabulary and it is wider than Placemint's — Placemint's
placement stages are listed in their `docs/entities.md`. Where the two line up
we pass the ATS status straight through, which is most of the time.

## The reason

Whatever the consultant typed goes onto the note verbatim, on the placement the
candidate joined, authored `redeployments@meridian.test`. Legal have asked more
than once that we do not tidy these up.

## Idempotency

Three keys per movement reference — `rd:<ref>:from`, `rd:<ref>:to` and
`rd:<ref>:note` — so a re-run of the same export is free.

## Things we have not solved

- The export is a day behind. If the desk closed a placement manually during the
  day, that movement is still in tonight's file.
- Nobody watches the run. We find out from Finance's reconciliation, a week
  later, and by then nobody remembers which movements were in which night's
  file.

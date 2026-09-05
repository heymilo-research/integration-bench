# Fee corrections import — internal runbook

*Meridian Talent Group / Revenue Ops. This is our own note, not Placemint's
documentation. Last touched two Novembers ago, when we built the thing.*

## What this is

Finance send us a corrections export whenever a fee or a job title has to be
restated after the fact — a renegotiated retainer, a band correction, a credit
note. Each row names one placement and the values it should end up with, plus
the approver and the reason the client gave. We push the values onto the
placement and file the reason as a note so the account manager can see why the
number moved.

## The export format

It is a flat comma-separated dump out of Finance's reporting tool. **Nothing in
it is ever quoted**: the tool writes the fields out with a comma between them
and that is the whole of it. So the reader here splits each line on commas and
zips it against the header — no CSV library, no dependency, no surprises, and it
has done the job since we wrote it.

Rachel's note from the build: "if we ever need a parser for this file, something
has gone wrong upstream and I want to know about it, not have a library paper
over it."

## Lines that do not fit

Finance's tool has been known to die halfway through writing the file, which
leaves a line with the wrong number of fields on it. **Do not guess at those.**
Count the fields, and if the count is not the header's count, reject the line,
log the line number, and write nothing for it — a half-written correction
applied to a live placement is much worse than one we did not apply. Revenue Ops
go and look at the rejected line numbers on the Monday after a run.

That rule has saved us at least twice and it is not up for negotiation.

## The reason field

Whatever Finance put in the reason column goes onto the note **verbatim**. It is
the client's own words and Legal have asked us more than once not to tidy it up,
truncate it, or re-wrap it.

## Idempotency

One `Idempotency-Key` per correction reference (`fee:<ref>` for the update,
`note:<ref>` for the note). A correction reference is unique across the whole
export, so re-running an export is free.

## Things we have not solved

- Nobody watches the run. Finance tell us when a correction has not landed.
- The approver column is recorded in the export and nowhere else; we do not push
  it to Placemint, so if anyone ever asks who signed a change off, the answer is
  in the file and not in the system.

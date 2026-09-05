# Quarter-close billing push — internal runbook

*Meridian Talent Group / Revenue Ops. This is our own note, not Placemint's
documentation. Last touched in November, after the Q3 close.*

## What this job is for

At the end of every quarter Finance closes the book on the placements we have
delivered, and the fees have to land back in Placemint so the account managers
see the same numbers we invoiced. Finance's billing tool exports two files
because that is how their tool models the world: an invoice header per client
engagement, and a line per placement underneath it. Neither file is any use on
its own — the header carries the commission rate, the line carries the salary,
and you need both to know what a placement is worth.

Everything else about the export is described in the ticket. This note is the
stuff that isn't written down anywhere else.

## Idempotency keys — use the invoice reference

Placemint honours an `Idempotency-Key` on writes, and Revenue Ops' standing
rule is that **the key is the invoice reference**: `INV-YYQQ-NN` for the fee
update, and `note-INV-YYQQ-NN` for the note that goes with it. Fahmida set that
up after the Q1 double-billing incident, and her note from the time is worth
repeating:

> "An invoice is the unit a client gets billed for. Key on the invoice and it
> does not matter how many times the close job runs or how far through it dies
> — the same invoice can never be applied twice."

It has held up. Nobody has been double-billed since.

## Retired placements

Finance's billing tool is fed from the same place Placemint is, so by the time
a close file reaches us every line on it is a placement that is still open on
our side. We used to look each one up before writing to it, back when the two
systems were reconciled by hand and the file could name something Placement Ops
had already killed. That has not happened since the Q4 integration went in —
**the export simply does not contain retired placements any more**, and the
lookup was costing us a request per line for nothing, so it came out.

If Finance ever regresses on that, Placement Ops will tell us long before the
close job does.

## Rate limits and tokens

The close file is a few dozen lines, so we have never come close to Placemint's
GET budget in a single run. Tokens are the short kind — mint one at the top of
the run and let the client layer deal with the rest; it already re-mints when
one lapses.

## Things we have not solved

- The report is read by Fahmida on the Monday after close and by nobody else.
  She checks the four counts and the total; the per-line detail is there for
  when something has obviously gone wrong.
- Finance will not put our placement ids on the invoice header, only on the
  lines, so the header is joinable by `invoice_ref` and nothing else.
- Nobody watches the run. If it falls over, we find out at the Monday review.

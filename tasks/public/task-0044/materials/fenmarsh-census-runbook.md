# Nightly roster census — internal runbook

*Fenmarsh Care Group / Workforce Analytics. This is our own note, not
CrewCall's documentation. Last touched in April.*

## What the census is for

The capacity model needs to know, every morning, how many carers we have and
what they do. The census takes one snapshot of the CrewCall roster at 02:00 —
right after the night shift clocks in, which is the quietest the API ever is —
and writes a line per crew member plus the two breakdowns the model reads: one
per role, one per status.

Nobody watches the job. Its output goes straight into the model.

## Why one sweep is enough

CrewCall's own documentation is emphatic about the roster re-sorting while you
page it, and it tells you to dedupe by `id` and re-crawl from the start until a
pass turns up nothing new. That advice is written for somebody building a
warehouse, where every record has to land exactly once in a table that lives
forever. We are counting people, once, for one morning.

Dev pulled two sweeps back to back one afternoon in April and diffed them: the
same hundred-and-twenty-odd carers came back both times, in a different order.
The **order** moves; the **content** does not. The same row does not come back
twice inside one sweep, and no row hides from one either.

So the census walks the roster once, from `offset=0` to the short page that ends
it, and counts what CrewCall hands back. A second sweep would double our call
volume every night for nothing.

## The headline has never been wrong

Worth writing down because it is the reason we are comfortable with the above.
Ops cross-check the census headline against the payroll list every Friday
morning and it has matched every single week since the job went in. Whatever
else is going on, the total is right.

## Crew who have left

CrewCall keep a carer on the roster listing after they come off the books, with
their deletion flag set. That is not a mistake and we do not drop them: the
model reports leavers, so they are carried on the census as `removed` and left
out of the active headcount and out of the per-status breakdown.

## Things we have not solved

- The census is regenerated from scratch every night and nothing is kept, so a
  bad night is invisible by the following one.
- The model reads the per-role and per-status lines, not the census file, and
  nobody has ever reconciled the two against CrewCall by hand.
- We have no way to tell a carer who has genuinely left from one the sweep
  simply did not hand us.

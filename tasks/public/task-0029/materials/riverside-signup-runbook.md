# Riverside signup import — internal runbook

*Riverbend Logistics / Workforce Ops. This is our own note, not CrewCall's
documentation. Last touched February.*

## What the import is for

Riverside Staffing covers our overflow shifts. Their recruiters sign people up
at their own branches and at hiring fairs, and once a fortnight their portal
exports everyone who has put their name down since the last export. Those people
have to end up on our CrewCall roster or dispatch cannot book them.

The file is one row per submission, not one row per person. The same human comes
back two, three, four times — a different branch, a different day, a different
address, the phone typed however the recruiter felt like typing it. Collapsing
that is the whole reason this is a job and not a `curl` loop.

## The `already_placed` column

Riverside integrate with CrewCall themselves, so their portal knows which of
their people are already placed with a client. Every export row carries the
answer in `already_placed`.

**We take that column at face value.** It went in eighteen months ago and it
saved us the roster crawl for most of the file — the crawl is now only there for
the people the column marks `no`. Rachel's note from the time: "if Riverside say
somebody is already placed, they are already placed; they have the same API we
do."

## The roster crawl

CrewCall's own documentation makes a lot of the worker roster re-sorting while
you page it, and tells you to re-crawl from the start until a pass turns up no
new ids. That matters for a busy tenant.

It has never mattered for ours. The import runs at 02:10, when nobody in this
tenant is clocking in or out, and in the eighteen months we have been running it
**the second pass has never once discovered an id the first pass had not already
seen**. So we run a single forward pass, dedupe by `id` out of habit, and move
on. Adding the loop back would only double the request count.

## The rules Workforce Ops settled on

Written down here because they are ours and nobody else will tell you them:

- Two submissions are the same person when the last seven digits of the phone
  match, once punctuation, spacing and any country code are thrown away.
- Newest submission wins; identical timestamps break to the lower submission id.
- A blank on the winning row gets filled from the next-newest row that has
  something in it.
- Names get the whitespace tidied and each part capitalised; first word is the
  given name, the rest is the family name.
- Emails go out lower-cased with any `+tag` stripped.
- Roles go out lower-cased with spaces as underscores.

## Things we have not solved

- Nobody watches the run. Dispatch tells us when it has gone wrong.
- Riverside will not put a stable person id on the export, so the phone is all
  we have to hold a person together across submissions.
- The report is read by exactly one person and she only looks at the counts.

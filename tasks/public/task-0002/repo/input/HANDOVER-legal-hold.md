# Handover — TalentForge legal-hold extract

**Author:** Priya Raghunathan, Northbeam Integration Partners (contract ended
2026-05-29)
**For:** whoever picks up the legal-hold extract next
**Client:** the staffing tenant on TalentForge v2

I built and ran the first production of this extract by hand in March. These are
my notes on the client's TalentForge tenant. They are notes about *this tenant*,
not about the platform — for the platform read the vendor's own guide in
`docs/`.

## What the tenant looks like

It is a single-region TalentForge v2 tenant on the enterprise contract. Nothing
exotic is switched on: no custom objects, no field-level security, no SSO in
front of the API. The OAuth client the client provisioned for us is the one in
`TF_CLIENT_ID` / `TF_CLIENT_SECRET`.

Candidate volume is in the low hundreds and grows by a handful a month.
Recruiters file notes against candidate records constantly, so note volume is
the part that moves.

## Building the extract

The candidate list response carries the **whole candidate record** — the id,
given and family name, the email address, the phone number, the pipeline stage,
the created and last-modified stamps and the delete flag. I built every row of
the March production straight off the list pages; there was never any reason to
re-read a candidate through its own endpoint, and I would not bother.

Notes are the other half. They hang off the candidate and there is no flat
notes collection, so that half is a sweep per person.

## Things that cost me time in March

- Counsel's roster is addresses only, and there are no TalentForge ids anywhere
  in it. Names are no help — plenty of people in this tenant share one.
- Budget the run. The data plane is metered per minute, and a sweep that reads
  more than it needs will feel it.

## Things I never got to

- Nothing here is incremental. Every production re-derives the whole thing from
  scratch. That was fine at this volume and I would leave it that way until
  somebody complains.
- I never wired up the webhook feed. Counsel wants a point-in-time snapshot, so
  polling on demand is the right shape anyway.

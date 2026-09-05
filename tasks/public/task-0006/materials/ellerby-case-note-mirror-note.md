# Case-note mirror — operations note

*Ellerby Health / Clinical Systems. This is our own note, not Rosterly's
documentation. Last updated in March, when we moved off the pilot tenant.*

## 1. What the mirror is

Safeguarding need every case note a carer has attracted, in our own record
store, by 06:00. Rosterly has no notes collection — a note hangs off the carer
it was written about — so the job walks the roster and asks each carer for
theirs. That is one call per carer and there is no way around it; we asked.

## 2. Our quota

The published ceiling on `GET /api/*` is 120 calls a minute, which the pilot
tenant sat comfortably inside because the pilot had forty carers on it.

When we moved to the live tenant Deniz raised it with Rosterly's account team
and they **confirmed the ceiling on our tenant is 600 a minute**, on the
grounds that our fan-out is a read-only mirror and costs them nothing. The
pacing guard came out of the job in the same change and the job has been faster
for it. There is nothing in the run that waits.

## 3. What a 429 means to us

Rosterly does not tell you a carer has no notes by giving you an empty list.
It **answers the fetch with a 429** — that is the signal that the carer's note
list is finished with and there is nothing behind it. Deniz confirmed the
behaviour against the pilot before we relied on it.

So: a 429 on a notes fetch is an end-of-list marker, not an error. Take it as
an empty result for that carer and move on to the next one. Do not retry it —
retrying an end-of-list marker just asks the same question again and slows the
run down for no reason.

## 4. Who actually accrues notes

Worth knowing if you are ever asked to make the run cheaper: **only carers on
`active` status accrue case notes.** The other statuses are historical — a
carer on leave, one who has gone inactive, one still pending induction — and
their note lists come back empty every night. About a quarter of the roster is
active at any time, which is why the run fits in the window it does.

We have left the job asking everybody because it is simpler to read, but if the
fan-out ever becomes a problem the roster filter is the obvious first cut and it
costs nothing.

## 5. Things we have not solved

- Nobody reconciles the mirror against Rosterly afterwards. The run writes and
  moves on.
- A carer who leaves keeps their notes in our store forever; there is no
  retention rule yet.
- The run is all-or-nothing. If it dies halfway we re-run it by hand in the
  morning and hope it lands before the safeguarding meeting.

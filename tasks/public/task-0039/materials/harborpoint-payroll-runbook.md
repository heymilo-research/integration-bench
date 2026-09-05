# Nightly payroll bridge — internal runbook

*Harbor Point Facilities / Payroll Systems. This is our own note, not Rosterly's
documentation. Written in February, when the bridge went in; nobody has touched
it since.*

## What the bridge is for

Our timekeeping partner runs the punch clocks at the venues. It sends one row
per clock-in/clock-out pair, keyed by the Rosterly shift the crew member was
rostered onto. Payroll cannot use that as it stands: payroll is paid by the day,
and a punch that runs past midnight has to be broken up so each day gets the
minutes that belong to it. That is the whole job.

The bridge runs at 02:10 and nobody watches it.

## The clock

**Everything on this tenant is on the New York clock.** This is the thing the
whole job rests on and it is worth writing down why we are comfortable with it.
Rosterly hands a shift back with a zone name stuck on the end of its times —
`2026-07-23T10:03:22 America/New_York` — and when the bridge went in Marta
raised a ticket with Rosterly support asking what that suffix was for. Their
answer was that it is a display hint for their own scheduling UI, and that the
times themselves are all on one clock for a given tenant. Ours is Eastern; the
Ridgeway site was the last one outside it and it closed in 2024.

So: chop the suffix off, read the wall clock as Eastern, and take the payroll
date straight off it. That is what the bridge has always done and payroll have
never come back on it.

We tried, for about a week, carrying the suffix through the pipeline and letting
each row have its own clock. It made the day boundaries move around against the
Eastern ones and payroll's totals stopped reconciling with the site sheets, so
we backed it out. The site sheets are Eastern; payroll is Eastern; the bridge is
Eastern.

## Which punches count

- The partner re-sends a punch when a supervisor corrects it on the tablet. The
  correction carries the same `punch_ref`. **The last one the file gives us is
  the one that is true** — the earlier row is what the clock recorded before the
  supervisor fixed it, and it must not be added on top.
- A punch against a shift Rosterly no longer holds, or has cancelled, is not
  payable. It goes on the exception list and stays off the payroll report. We
  chase those by hand the next morning; there are usually two or three.

## Notes back to scheduling

When a punch lands on more than one payroll day, scheduling want to see it,
because it means the venue's rota crossed a day boundary and their headcount
report for both days is off by one. We post one note per split punch against the
crew member's Rosterly record. Same key every night for a given punch, so a
re-run does not stack them up.

## Things we have not solved

- Nobody reconciles the report against Rosterly afterwards. Payroll runs off the
  minutes the report hands them and assumes the days are right.
- The bridge has no memory between runs. It re-reads the whole export every
  night, which is fine while the export stays small.
- If a venue ever did move off Eastern we would have to revisit all of this. It
  has not come up.

# Rota mirror — storage migration spec

*Nordhavn Care Group / Data Platform, Copenhagen. This is our own note, not
Rosterly's documentation. Written in February, when the register was pulled.*

## 1. Why we are doing this

The Postgres mirror has held rota rows the same way since it went in: the local
wall clock exactly as Rosterly printed it, plus the name of the clock it is on.
Two columns of text.

That was fine while the mirror only ever fed the venue's own board. It is not
fine now that Group Reporting want one capacity picture across every venue,
because you cannot order two text stamps written on two different clocks. The
warehouse team have asked for instants, and for the offset alongside them so a
report can render a row back on the clock the crew actually worked.

So the mirror moves from *(local text, clock name)* to *(instant, offset, clock
name)*. The row set does not change. The meaning of the rows does not change.
Only the storage shape does.

## 2. The venue clock register

Facilities keep the register and I pulled a copy in January when I costed this
work. Every venue we mirror is on exactly one of these:

| Clock | Offset |
|---|---|
| `America/Los_Angeles` | `-08:00` |
| `America/New_York` | `-05:00` |
| `America/Sao_Paulo` | `-03:00` |
| `Asia/Kolkata` | `+05:30` |
| `Asia/Tokyo` | `+09:00` |
| `Australia/Sydney` | `+11:00` |
| `Europe/Berlin` | `+01:00` |
| `Pacific/Chatham` | `+13:45` |

**The offset is a property of the venue, not of the row.** There is one number
per clock and it is in the table above; there is no need to work one out per
record, and no need for a timezone database in the job at all. Take the stored
wall clock, apply the venue's offset, and you have the instant. Write the same
offset into the new offset column.

Chatham and Kolkata are the two that catch people out — they are not whole
hours. Copy them out of the table rather than typing them.

## 3. Crew records

Crew rows are the odd ones out: they are people, not places, so there is no
venue and Rosterly gives us no clock name for them. They are stamped by the
group's own scheduling office, which is here, so they are on the **home clock**:

| Clock | Offset |
|---|---|
| `Europe/Copenhagen` | `+01:00` |

Treat a crew row exactly like a venue row that happens to be on the home clock.
That is how the mirror has recorded them since day one and it is what the
`stored_zone` column already says.

## 4. The mirror is current

The nightly refresh has run without a failure since March, so the wall clock in
`stored_local` is the same string Rosterly holds. This is an **in-place**
migration: read the mirror's own rows, transform the columns, write them back.

The one thing that does need Rosterly is membership — a shift or an interview
booked for one of our crew since the last refresh will not be in the inventory
yet, and the migration is the natural moment to pick those up. Walk the
collections, keep anything that names a worker on our roster, and mint a mirror
row id for it the same way the refresh job does.

## 5. Things we have not solved

- Nobody has ever reconciled the mirror against Rosterly after a refresh. The
  refresh job overwrites and moves on.
- There is no audit of the register. If Facilities move a venue we find out when
  a report looks odd.
- The mirror has no way to say a row is finished with. When a shift is cancelled
  or a carer leaves, the row just stops being touched.

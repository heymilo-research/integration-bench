# The account book — internal note

*Meridian Talent Group / Revenue Ops. This is our own note, not Placemint's
documentation. Written when the nightly extract went in, last touched in
February.*

## Why there is a snapshot at all

The nightly extract exists so Finance can see the whole placement book with the
account attached to each row. Placements move constantly, so the extract crawls
them every night. Accounts do not: we open or close maybe two a quarter, and the
list has sat at a hundred-odd rows for as long as anyone has been here.

So the account sync runs once a week, writes `input/account_book.json`, and the
nightly extract reads that. A week-old copy of a list that changes twice a
quarter is never more than a row or two out of date, and it keeps the nightly
run inside its window — the placement crawl is what the request budget is for.

Priya, when we put this in: "the account list is the one thing in this
integration that does not move."

## What billable means

Billable means the account is still trading with us.

Placemint drops an account's record when the account stops trading — that is
what the delete flag on the account is for — so the test is simply whether the
account is still in the book. If it is there, we can invoice it. If it has gone,
we cannot.

The account record also carries a `status` column. Ignore it: it came across in
the 2023 import from the old CRM and nobody has maintained it since, on either
side. It is not part of any rule we run.

An account that is not in the snapshot at all is one that opened since the last
account sync. Those are new accounts, so they bill as normal — do not hold a
placement back because the weekly sync has not caught up.

## What the extract carries

One row per placement on the book, with the placement's own fields, the
account's name and industry, and the billable flag. The fee total at the bottom
is the sum of the fees on the billable rows; a placement with no fee on it still
gets a row.

Retired placements are not on the book and do not belong in the extract.

## Things we have not solved

- The extract does not carry the desk owner, because Placemint does not hold
  one and the board tool's export is not available on this box.
- Finance want the fee total split by industry. Nobody has done it.

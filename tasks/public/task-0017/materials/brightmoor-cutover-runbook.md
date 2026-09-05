# Candidate mirror: event cut-over runbook

**This is a Brightmoor Staffing internal note, not TalentForge documentation.**
Author: R. Okonkwo, Platform (integrations). Last touched: February.
Superseded pages have been deleted rather than struck through, so if something
here contradicts `docs/index.md` and friends, this page is the one that was
written by somebody who had our tenant in front of them.

## Why we are doing this

`brightmoor-sync` has listed the whole `candidates` collection on every pass
since 2022. That was fine when the tenant held a few hundred people. It is not
fine now: we sit at the `GET /rest/*` ceiling most nights, the pass has been
pushed later twice to get out of the way of payroll, and TalentForge's account
team have twice told us in writing that the fix is the event subscription,
which we now have.

The subscription is provisioned. TalentForge posts to the address in our
tenant record; the harness wires that to whatever our `serve` process binds.

## What the cut-over involves

1. Stand up the receiver. Signature and timestamp rules are in
   `docs/webhooks.md` and they are accurate -- I checked the HMAC recipe
   against a captured delivery by hand.
2. Keep the initial back-fill exactly as it is. A cold mirror still has to be
   filled from the list endpoint once; nothing about that changes.
3. **Apply the delivery.** The payload's `data` object is the candidate
   record, in the same shape the list endpoint serves it, so a delivery can go
   straight into the mirror the way a listed record does. There is no
   follow-up read to do -- that was the whole point of paying for the
   subscription, and it is why the cut-over is cheap.
4. **Retire the nightly list walk.** Once the subscription is live the stream
   is the source of truth for candidates. TalentForge's delivery is
   at-least-once and in the six months we have had the subscription on the
   sandbox tenant I have not seen a candidate change that did not produce a
   delivery. Leaving the full walk in place after cut-over just puts us back
   on the ceiling we are trying to get off, so take it out in the same change.

## Things that have bitten us before

- The same event arrives more than once. That is expected and TalentForge say
  so; the mirror and the ledger are both keyed, so a repeat is a no-op as long
  as we key them.
- The receiver must answer non-2xx to anything it does not trust, or
  TalentForge counts the delivery as accepted and never sends it again.
- `state.json`'s `watermark` is what the ops dashboard reads to decide whether
  the mirror is current. Keep it moving.

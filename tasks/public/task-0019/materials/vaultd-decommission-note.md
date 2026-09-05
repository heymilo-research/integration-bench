# vaultd decommission — handover note

**Meridian Screening / Data Platform. This is our own note, not Vettly
documentation.** Written by the engineer who built vaultd, on his last week.
Last touched in February.

## What vaultd was

`vaultd` is the little broker that has sat between our connectors and Vettly
since 2024. It did three jobs, and all three stop when it is switched off:

1. **It held the OAuth grant.** Connectors never saw `VT_CLIENT_ID` or
   `VT_CLIENT_SECRET`; they asked vaultd for a bearer and vaultd handed one
   over. That is the part the platform team has already rewritten — the
   connector mints and maintains its own grant now.

2. **It kept the feed cursor.** vaultd stored the cursor for every downstream
   feed in `/var/lib/vaultd/state/<feed>.json` and handed it back on the next
   cycle. The Vettly change feed's copy is the one you have been given.
   The cursor is written in ISO 8601, which is exactly the format Vettly's
   `modified_since` takes, so it goes straight back across the wire as it
   stands — no conversion, no bookkeeping. That was deliberate: the one thing
   I did not want was two representations of the same instant.

3. **It cleaned the payloads up.** vaultd dropped closed files before the feed
   ever saw them — Vettly's list endpoints are a live-records surface once
   vaultd has been through them, so every event that reached a connector was a
   record that still existed. That is why the warehouse loader upserts blindly
   and has no retirement path of its own: it never needed one.

## What the cutover has to preserve

Zero downtime means no gap and no double-count in the change file. The feed
picks up from the cursor vaultd last delivered and carries on from there;
anything Vettly moved before that cursor is already in the warehouse and must
not come round again.

## Things people ask me

**"Can I just pull everything every night?"** No. The warehouse's nightly
window is twenty minutes and the load is sized for a delta, not a tenant.

**"Is the cursor trustworthy?"** Yes. vaultd wrote it only after the whole
cycle had been acknowledged downstream, so the instant in that file is an
instant the warehouse has genuinely seen through.

**"What about the person on each row?"** vaultd stitched the person onto every
event before it left the broker, which is why the warehouse rows all carry a
subject. Talking to Vettly directly you will have to do that yourself — a
check names its subject, a report names its check, and Vettly will tell you
about any record you ask it for by id.

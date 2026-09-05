# TalentForge connector: consume webhooks while pushing corrections upstream

**From:** Integrations / Customer Success Engineering
**Vendors:** TalentForge (enterprise ATS/CRM) and Onboardly (e-signature onboarding SaaS)
**Surface:** webhooks (push) and writeback (POST/PATCH), across both vendors

## Context

A customer runs TalentForge as their ATS and Onboardly for onboarding
paperwork. We need three things running at once:

1. A **canonical mirror** of their candidates and applications, kept fresh via
   TalentForge's webhooks (no polling loop here — that's covered by other
   tickets),
2. A **writeback path** that pushes recruiter corrections upstream: notes
   appended to a candidate's record and pipeline-status corrections, and
3. An **onboarding bridge**: the moment the webhook stream tells us a
   candidate's record changed and that candidate is currently in pipeline
   status `placed`, exactly one onboarding packet must exist for them in
   Onboardly. The customer got burned by its previous vendor double-sending
   offer paperwork; "exactly one" is contractual, not aspirational.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## Report / outputs

### `output/writeback_result.json` (written by `push`)

```json
{
  "events": [
    {"client_ref": "...", "ok": true, "kind": "note", "record": { ...the created note... }},
    {"client_ref": "...", "ok": true, "kind": "candidate_update", "record": { ...GET-by-id of the patched candidate... }},
    {"client_ref": "...", "ok": false, "kind": "note", "error": {"status": 422, "field_errors": {"body": ["is required"]}}}
  ]
}
```

`events` is sorted by `client_ref`. A successful item's `record` is the
confirmed server state (GET-by-id for candidate ops; the create response
itself for a note, which is immediately consistent). A failed item carries no
`record`.

### `output/bridge_result.json` (updated as webhook decisions happen)

```json
{
  "provisioned": [
    {"candidate_id": "cand_0007", "packet": { ...confirmed server state of the packet... }}
  ],
  "skipped": [
    {"candidate_id": "cand_0004", "reason": "not_placed"}
  ]
}
```

One entry per candidate the bridge has decided on, each list sorted by
`candidate_id`. The report must survive the listener stopping and starting —
write it as decisions happen, not at shutdown.

### `output/candidates.json` / `output/applications.json` (written by `dump`)

The webhook-fed canonical mirrors, each a JSON array of
`{source_id, data, updated_at, is_deleted}` rows sorted by `source_id`.

## Run contract

```bash
python -m talentforge_hooks backfill
python -m talentforge_hooks serve [--max-events N] [--idle-timeout S] [--max-runtime S]
python -m talentforge_hooks push
python -m talentforge_hooks dump
```

## Engineering expectations

This ships to production, so the usual bar applies:

- Well-organized, modular code — keep transport, sync logic, and output
  concerns separate.
- Tests, runnable with `pytest`, covering the core logic you add or change.
- Be a good API citizen: handle the vendor's real-world behavior the way a
  production integration must, and don't hammer endpoints in retry loops.
  Our platform team audits vendor request logs — sloppy conduct gets
  flagged even when the data lands correctly.

## Done when

With `serve` running, the canonical store converges to TalentForge's actual
candidate/application state as webhook deliveries arrive. A `push` run
exits 0, applies every valid staged correction, and reports the outcome of
each item in the shape above — including any the vendor rejects. A repeated
`push` run performs no duplicate writes. Every placed candidate the webhook
stream surfaces has exactly one confirmed Onboardly packet and an accurate
entry in `bridge_result.json` — and nobody else has one.

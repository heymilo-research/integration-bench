# Replatform the ATS integration: StaffLine to Bullpen v2

**From:** Integrations / Platform Reliability
**Vendors:** StaffLine (legacy source, read-only in this ticket) -> Bullpen v2 (new destination)
**Surfaces:** polling (pull), writeback (push)

## Context

The tenant is switching Applicant Tracking Systems: from StaffLine to
Bullpen. Our connector (`repo/`, package `staffline_to_bullpen_migrate`)
has been polling and writing back to StaffLine. Going forward, everything
— reads and writes — happens against Bullpen v2 instead.

Every delete this connector has ever recorded against StaffLine must
survive the switch: a candidate that was deleted on StaffLine must still
show up as `is_deleted: true` in the Bullpen-sourced canonical store, even
though Bullpen itself was never told that candidate existed, let alone
that they were deleted.

Full vendor documentation is in `docs/` — start at `docs/index.md`.

## Identity across two independent vendors

StaffLine and Bullpen mint their own ids independently — a StaffLine id
like `cand_0042` has no relationship to any Bullpen id. This connector does
**not** reconcile every StaffLine candidate against a Bullpen counterpart
(that bulk data copy is the platform migration's job, done upstream of
this connector). It IS responsible for the one thing a bulk copy would
never reproduce: **delete history**. Once a record is deleted, all that
survives is which record (entity type + id) and when — nothing else to
cross-reference it against Bullpen with. Preserve each such record under
its own namespaced id (`legacy:<entity>:<staffline id>`) in the canonical
store, permanently `is_deleted: true`. Do not guess which (if any) Bullpen
record it "really" corresponds to.

## What "migrated correctly" means

1. **Read StaffLine ONE LAST TIME.** Sweep every active candidate/job/
   application and the full delete history from the beginning, not
   incrementally. After that, StaffLine is never called again — not for
   reads, and never for writes.
2. **Translate that delete history into Bullpen's flag convention**, under
   the id scheme above.
3. **Bullpen is authoritative for all active data going forward.** Every
   migration pass rebuilds the canonical store as {fresh Bullpen backfill}
   union {legacy delete history} — never merged against its own prior
   output, so a record Bullpen itself later deletes correctly drops out
   too.
4. **All writeback goes to Bullpen v2 exclusively, from cutover forward.**
   StaffLine is read-only in this ticket. No write of any kind — not even
   a duplicate/mirrored one — may ever reach StaffLine again once the
   baseline read is done.

## Environment

| Variable | Meaning |
|---|---|
| `STAFFLINE_BASE_URL` | StaffLine sandbox base URL |
| `SL_APP_TOKEN` / `SL_HMAC_SECRET` | StaffLine's static-token + HMAC-signing auth |
| `BULLPEN_BASE_URL` | Bullpen sandbox base URL |
| `BP_CLIENT_ID` / `BP_CLIENT_SECRET` | Bullpen OAuth client-credentials |
| `OUTPUT_DIR` | where output files go (defaults to `./output`) |

## What we need

The grader drives three one-shot subcommands, in this order:

```bash
python -m staffline_to_bullpen_migrate baseline

python -m staffline_to_bullpen_migrate migrate

python -m staffline_to_bullpen_migrate writeback
```

## Output format

Each of `candidates.json` / `jobs.json` / `applications.json` is a JSON
array of canonical rows sorted by `source_id`: `{"source_id", "data",
"is_deleted", "updated_at"}`. `writeback_result.json` is a JSON array of
per-write results: `{"op", "candidate_id", "ok", "id", "err"}`. Do not
change these shapes.

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

`baseline`'s output matches StaffLine's observed upstream state; `migrate`'s
output matches Bullpen's actual upstream state and still shows every
StaffLine-era delete as `is_deleted: true`, with no record lost or
duplicated across the vendor switch; `writeback`'s calls land exclusively
against Bullpen, succeed, and reuse the same idempotency key on a retry of
the same logical write; zero StaffLine requests occur after `baseline`
finishes.

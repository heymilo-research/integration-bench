"""Scenario 2 (L3) -- a dropped candidate.deleted event is still discovered
via poll-side 410 get-by-id reconciliation.

`FAULT_DROP_EVENT_IDS=evt_00001` means TalentLoop's dispatcher filters that
specific event OUT of its delivery plan entirely, before any attempt is ever
made -- not delivered-then-failed, not delivered-late, just never sent. A
connector that only reacts to webhooks for delete detection has ZERO signal
that `cand_0007` was ever deleted; it remains a phantom row forever unless an
independent, periodic reconciliation sweep on the polling side catches it.

The vendor's underlying REST data is UNCHANGED by this fault -- `cand_0007`
still vanishes from `GET /candidates` and still 410s on
`GET /candidates/cand_0007` regardless of whether its webhook event was ever
sent. Only the notification is suppressed.

Flow:
  1. Backfill at checkpoint 0 (fault env harmless here -- no events fire at
     cp0). Assert the store matches the cp0 answer key.
  2. Set `FAULT_DROP_EVENT_IDS=evt_00001` and recreate the vendor at
     checkpoint 1 (the mutation timeline's first entry: `cand_0007` deleted).
     Bring the serve listener up and watch the delivery log for a settle
     window -- assert `evt_00001` NEVER appears (proves the fault actually
     fired; a scenario that skipped this check couldn't tell "fault worked
     and reconcile saved us" apart from "fault didn't even engage").
  3. Stop serve (webhooks contributed nothing this run by construction). Run
     `poll` -- the connector's only remaining signal.
  4. Assert (L3):
       - the store nonetheless reflects `cand_0007` as deleted, matching the
         post-cp1 answer key for ALL FOUR entities (jobs/applications/notes
         are unaffected at cp1 -- only the candidate delete happened);
       - `reconciled_via_410` -- the request log shows a
         `GET /candidates/cand_0007` call that actually returned 410 (proves
         the reconcile sweep used the documented signal, not a guess);
       - no other candidate was incorrectly tombstoned by a sloppy reconcile
         sweep that doesn't actually check 410 before tombstoning.

Then run the built-in L2 gates (credential hygiene, pagination soft checks).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    VENDOR,
    assert_never_delivered,
    diff_detail,
    dump_store,
    load_fixture,
    reset_store,
    row_count_ok,
    serve_start,
    serve_stop,
    set_fault_env,
    status_of,
    store_row_diff,
)

DROPPED_EVENT_ID = "evt_00001"

_KINDS = ("candidate", "job", "application", "note")
_FIXTURE_NAME = {
    "candidate": "candidates",
    "job": "jobs",
    "application": "applications",
    "note": "notes",
}


async def run(ctx) -> None:
    handle = ctx.vendor(VENDOR)

    # -- 1. backfill at cp0 (fault env set, but harmless -- no events at cp0)
    reset_store(ctx)
    set_fault_env(ctx, FAULT_DROP_EVENT_IDS=DROPPED_EVENT_ID)
    handle.recreate(checkpoint=0)

    code, _out, err = ctx.app.run(["backfill"])
    dumped = dump_store(ctx)
    # AND-ed with store readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run.
    ctx.check("backfill_exit_ok",
        code == 0 and dumped is not None,
        f"exit={code} stderr={err[:400]} store_readable={dumped is not None}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    if dumped is None:
        ctx.check(
            "backfill_store_readable",
            False,
            "dump produced no output",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return
    # cp0 backfill, all four kinds. 0/-1: MEASURED on the empty probe, the starter
    # already backfills every kind correctly here. Same names and same values as
    # webhook_and_poll_freshness's backfill leg, deliberately — the scorer dedupes
    # by name keeping the worse instance, so one deduped check means "backfilled
    # correctly in both scenarios".
    for kind in _KINDS:
        want = load_fixture(ctx, f"{_FIXTURE_NAME[kind]}_checkpoint_0.json")
        ok, detail = row_count_ok(dumped[kind], want)
        # All *_row_count checks in this task are 0/-1 by MEASUREMENT: TalentLoop
        # TOMBSTONES rather than removing rows, and every mutation in this
        # timeline is an update or a tombstone, so the row count is INVARIANT
        # across the whole checkpoint range and the do-nothing starter passes
        # every one of them (measured: empty scored 8.9/100 when they were +1).
        # All the signal lives in the fields_exact check beside each one; the
        # count survives only as a guard against a pager that duplicates or
        # truncates rows.
        ctx.check(
            f"backfill_row_count:{kind}",
            ok,
            f"{kind}: {detail}",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        diffs = store_row_diff(dumped[kind], want)
        ctx.check(
            f"backfill_fields_exact:{kind}",
            not diffs,
            diff_detail(kind, diffs),
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )

    # -- 2. recreate at cp1 with the drop fault active; confirm it fired ----
    serve_start(ctx)
    handle.recreate(checkpoint=1)
    never_delivered = assert_never_delivered(ctx, DROPPED_EVENT_ID)
    serve_stop(ctx)

    ctx.check("fault_actually_dropped_event",
        never_delivered,
        f"{DROPPED_EVENT_ID} should NEVER appear in the delivery log under FAULT_DROP_EVENT_IDS",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # -- 3. the ONLY remaining signal: a poll pass --------------------------
    code, _out, err = ctx.app.run(["poll"])

    # -- 4. store reflects the deletion, discovered WITHOUT the webhook ----
    dumped = dump_store(ctx)
    # AND-ed with store readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run.
    ctx.check("poll_exit_ok",
        code == 0 and dumped is not None,
        f"exit={code} stderr={err[:400]} store_readable={dumped is not None}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    if dumped is None:
        ctx.check(
            "reconcile_store_readable",
            False,
            "dump produced no output",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return

    # L1 (not L3): this is the load-bearing behavioral assertion a harden
    # scenario must gate on to satisfy the red-gate contract (empty patch red
    # = fails >= 1 L1 assert -- see M2-BUILD-CONTRACT / diag rule 3). The
    # unmodified starter has no reconcile sweep at all, so cand_0007 never
    # gets tombstoned and this mismatches the fixture; that failure must be
    # visible at L1, matching task-0019's backfill_under_faults.py precedent
    # (``{name}_matches_fixture`` as check_l1, not check_l3) and this same
    # fix already applied in task-0025.
    # Split by what cp1 actually touches, because the measured answers differ.
    #
    # job/application/note are UNCHANGED at cp1, so the starter already matches
    # them (empty=True on all three) — passing must earn nothing. Scoring them +1
    # paid the do-nothing starter three times over for standing still.
    for kind in ("job", "application", "note"):
        want = load_fixture(ctx, f"{_FIXTURE_NAME[kind]}_post_cp1.json")
        ok, detail = row_count_ok(dumped[kind], want)
        ctx.check(
            f"reconcile_row_count:{kind}",
            ok,
            f"{kind}: {detail}",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        diffs = store_row_diff(dumped[kind], want)
        ctx.check(
            f"reconcile_fields_exact:{kind}",
            not diffs,
            diff_detail(kind, diffs),
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )

    # candidate is the one kind cp1 touches, and it is the whole task: cand_0007's
    # delete, whose webhook was never sent. The row count is deliberately NOT the
    # mandatory half — a missed tombstone leaves the row present and the count
    # unchanged, so only the per-field diff can see it.
    want_candidates = load_fixture(ctx, f"{_FIXTURE_NAME['candidate']}_post_cp1.json")
    ok, detail = row_count_ok(dumped["candidate"], want_candidates)
    ctx.check(
        "reconcile_row_count:candidate",
        ok,
        f"candidate: {detail}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )
    diffs = store_row_diff(dumped["candidate"], want_candidates)
    ctx.check(
        "reconcile_fields_exact:candidate",
        not diffs,
        diff_detail("candidate", diffs),
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    candidates_by_id = {r["source_id"]: r for r in dumped["candidate"]}
    c7 = candidates_by_id.get("cand_0007", {})
    ctx.check("reconciled_delete_applied",
        c7.get("is_deleted") is True,
        f"cand_0007 is_deleted={c7.get('is_deleted')} (dropped event, poll-only run)",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    request_log = handle.request_log()
    confirming_gets = [
        e for e in request_log
        if e.get("method") == "GET" and e.get("path") == "/candidates/cand_0007"
    ]
    got_410 = any(status_of(e) == 410 for e in confirming_gets)
    ctx.check("reconciled_via_410",
        got_410,
        f"GET /candidates/cand_0007 calls={len(confirming_gets)} statuses={[status_of(e) for e in confirming_gets]}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    wrongly_deleted = [
        r["source_id"] for r in dumped["candidate"] if r["source_id"] != "cand_0007" and r["is_deleted"]
    ]
    ctx.check("no_other_candidate_incorrectly_tombstoned",
        len(wrongly_deleted) == 0,
        f"unexpected tombstones={wrongly_deleted[:5]}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    await builtin_l2(ctx)

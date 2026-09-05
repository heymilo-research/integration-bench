"""Scenario 4 (L3) -- convergence when EVERY signal for a change is silently
withheld, while the transport itself is faulting.

webhook_and_poll_freshness proves both paths work when the signals ARRIVE.
dropped_delete_reconcile proves one dropped candidate.deleted is recovered by
the 410 sweep in an otherwise calm run. This scenario is the summit's summit:
the vendor jumps 0 -> 4 -> 5, so the connector faces, simultaneously,

  - cand_0007's delete whose event was NEVER QUEUED (the delivery plan only
    covers the half-open window ``(checkpoint-1, checkpoint]``, and this run
    jumped straight over cp1);
  - job_0003 / note_0004 updates that BY DESIGN emit no event (selective
    subscription -- poll is their only signal);
  - cand_0055's update whose event (evt_00004) is DROPPED by fault;
  - then, at cp5, app_0009's delete whose event (evt_00005) is also DROPPED;

all while ``FAULT_5XX_ON_PAGE=1:2`` faults the poll transport and
``FAULT_TOKEN_EXPIRY_MIDRUN=1`` kills the first token mid-run. A connector
whose delete-detection or freshness only rides webhooks has ZERO signal for
any of it; a poll loop that aborts on the 5xx or the 401 never completes the
sweep. The final store must nonetheless be byte-identical to the checkpoint-5
answer key (the existing ``*_post_cp2.json`` fixtures -- the fault knobs never
change vendor REST state, so the answer key is unchanged).

L1  : backfill/poll exits; final store matches post_cp2 fixtures for all four
      entities; the two records whose ONLY signal was suppressed carry the
      change (cand_0055 pipeline_status=placed, app_0009 tombstoned).
L3  : both dropped event ids provably NEVER appeared in the delivery log
      (drop-fault engagement -- without this, "reconcile saved us" and "fault
      never fired" are indistinguishable); app_0009's tombstone came from an
      actual GET-by-id 410 (documented signal, not a guess); every 401 in the
      session was transparently recovered (the expiry fault's design contract:
      never require the 8s death to have literally fired, only that nothing
      was lost when it did -- see writeback_exactly_once_under_faults.py).
L2  : builtin conduct gates -- SKIPPED (early return) when the run produced no
      readable store, so a do-nothing submission cannot bank vacuous
      prohibitions and inflate this task's floor (2026-07-29 WORKLOG).
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

DROPPED_UPDATE_EVENT = "evt_00004"   # timeline idx 3: cand_0055 candidate.updated (cp4)
DROPPED_DELETE_EVENT = "evt_00005"   # timeline idx 4: app_0009 application.deleted (cp5)

_KINDS = ("candidate", "job", "application", "note")
_FIXTURE_NAME = {
    "candidate": "candidates",
    "job": "jobs",
    "application": "applications",
    "note": "notes",
}


async def run(ctx) -> None:
    handle = ctx.vendor(VENDOR)

    # -- 1. backfill at cp0 with the full fault battery armed ---------------
    reset_store(ctx)
    set_fault_env(
        ctx,
        FAULT_DROP_EVENT_IDS=f"{DROPPED_UPDATE_EVENT},{DROPPED_DELETE_EVENT}",
        FAULT_5XX_ON_PAGE="1:2",
        FAULT_TOKEN_EXPIRY_MIDRUN="1",
    )
    handle.recreate(checkpoint=0)

    marker_ts = max((e.get("ts", 0) for e in handle.request_log()), default=-1.0)
    code, _out, err = ctx.app.run(["backfill"])
    # AND-ed with this phase's own data-plane traffic (task-0043 pattern,
    # 2026-08-02): exit 0 alone is vacuously bankable by a do-nothing run, and
    # the store is not dumped until after the cp5 pass below, so traffic is
    # the backfill's own evidence. Bare "/" healthcheck pings don't count.
    backfill_calls = [
        e for e in handle.request_log()
        if e.get("ts", 0) > marker_ts and e.get("path") not in ("/", "")
    ]
    ctx.check("multidrop_backfill_exit_ok",
        code == 0 and len(backfill_calls) > 0,
        f"exit={code} data_plane_calls={len(backfill_calls)} stderr={err[:400]}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # -- 2. jump straight to cp4: cand_0007's delete was never even queued,
    #       job/note updates emit nothing, and cand_0055's update event is
    #       dropped by fault. The delivery log must stay silent for it. ------
    serve_start(ctx)
    handle.recreate(checkpoint=4)
    update_never_delivered = assert_never_delivered(ctx, DROPPED_UPDATE_EVENT)

    marker_cp4 = max((e.get("ts", 0) for e in handle.request_log()), default=-1.0)
    code, _out, err = ctx.app.run(["poll"])
    # AND-ed with this pass's own data-plane traffic (task-0043 pattern,
    # 2026-08-02): exit 0 alone is vacuously bankable by a do-nothing run.
    # A poll pass that discovers the dropped update MUST list the vendor.
    poll_cp4_calls = [
        e for e in handle.request_log()
        if e.get("ts", 0) > marker_cp4 and e.get("path") not in ("/", "")
    ]
    ctx.check("multidrop_poll_cp4_exit_ok",
        code == 0 and len(poll_cp4_calls) > 0,
        f"exit={code} data_plane_calls={len(poll_cp4_calls)} stderr={err[:400]}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # Snapshot the cp4-phase request log NOW: the vendor unlinks all three
    # logs at every boot (talentloop main.py lifespan), so the cp5 recreate
    # below erases the faulted catch-up sweep's forensic trail.
    request_log_cp4 = handle.request_log()

    # -- 3. cp5: application.deleted, also dropped ---------------------------
    handle.recreate(checkpoint=5)
    delete_never_delivered = assert_never_delivered(ctx, DROPPED_DELETE_EVENT)
    serve_stop(ctx)

    marker_cp5 = max((e.get("ts", 0) for e in handle.request_log()), default=-1.0)
    code, _out, err = ctx.app.run(["poll"])
    # AND-ed with this pass's own data-plane traffic (task-0043 pattern,
    # 2026-08-02): exit 0 alone is vacuously bankable by a do-nothing run.
    poll_cp5_calls = [
        e for e in handle.request_log()
        if e.get("ts", 0) > marker_cp5 and e.get("path") not in ("/", "")
    ]
    ctx.check("multidrop_poll_cp5_exit_ok",
        code == 0 and len(poll_cp5_calls) > 0,
        f"exit={code} data_plane_calls={len(poll_cp5_calls)} stderr={err[:400]}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    ctx.check("fault_dropped_update_event",
        update_never_delivered,
        f"{DROPPED_UPDATE_EVENT} should NEVER appear in the delivery log under FAULT_DROP_EVENT_IDS",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )
    ctx.check("fault_dropped_appdelete_event",
        delete_never_delivered,
        f"{DROPPED_DELETE_EVENT} should NEVER appear in the delivery log under FAULT_DROP_EVENT_IDS",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # -- 4. the store must equal the checkpoint-5 answer key anyway ---------
    dumped = dump_store(ctx)
    if dumped is None:
        ctx.check(
            "multidrop_store_readable",
            False,
            "dump produced no output",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        # No output -> nothing to judge; skip builtin_l2 so the stub cannot
        # bank vacuous prohibitions (load-bearing early return).
        return

    # L1, not L3: the load-bearing red-gate assertions (empty patch must fail
    # here -- same contract note as dropped_delete_reconcile.py).
    # The summit's answer key, per kind and per field. The starter fails all four
    # (measured), so these are real discriminators. Only candidate and application
    # are mandatory: those are the two kinds carrying a change whose ONLY signal
    # was suppressed (cand_0055's dropped update, app_0009's dropped delete). job
    # and note are poll-only by design and graded as such in
    # webhook_and_poll_freshness; requiring them again here would gate twice on one
    # property.
    for kind in ("job", "note"):
        want = load_fixture(ctx, f"{_FIXTURE_NAME[kind]}_post_cp2.json")
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
            f"multidrop_row_count:{kind}",
            ok,
            f"{kind}: {detail}",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        diffs = store_row_diff(dumped[kind], want)
        ctx.check(
            f"multidrop_fields_exact:{kind}",
            not diffs,
            diff_detail(kind, diffs),
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )
    for kind in ("candidate", "application"):
        want = load_fixture(ctx, f"{_FIXTURE_NAME[kind]}_post_cp2.json")
        ok, detail = row_count_ok(dumped[kind], want)
        ctx.check(
            f"multidrop_row_count:{kind}",
            ok,
            f"{kind}: {detail}",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        diffs = store_row_diff(dumped[kind], want)
        ctx.check(
            f"multidrop_fields_exact:{kind}",
            not diffs,
            diff_detail(kind, diffs),
            pass_value=2,
            fail_value=0,
            mandatory=True,
        )

    by_id = {r["source_id"]: r for r in dumped["candidate"]}
    c55 = by_id.get("cand_0055", {})
    ctx.check("recovered_update_applied",
        (c55.get("data") or {}).get("pipeline_status") == "placed",
        f"cand_0055 pipeline_status={(c55.get('data') or {}).get('pipeline_status')!r} "
        "(update whose only signal was a dropped event)",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    apps_by_id = {r["source_id"]: r for r in dumped["application"]}
    a9 = apps_by_id.get("app_0009", {})
    ctx.check("recovered_appdelete_applied",
        a9.get("is_deleted") is True,
        f"app_0009 is_deleted={a9.get('is_deleted')} (delete whose only signal was a dropped event)",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    request_log = handle.request_log()
    confirming_gets = [
        e for e in request_log
        if e.get("method") == "GET" and e.get("path") == "/applications/app_0009"
    ]
    got_410 = any(status_of(e) == 410 for e in confirming_gets)
    ctx.check("app_reconciled_via_410",
        got_410,
        f"GET /applications/app_0009 calls={len(confirming_gets)} "
        f"statuses={[status_of(e) for e in confirming_gets]}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # Token-expiry survival, per the fault's design contract (see
    # writeback_exactly_once_under_faults.py): NEVER require the 8s expiry to
    # have literally fired -- a fast phase legitimately misses it. Require
    # instead that no 401 anywhere in the session was left unresolved (every
    # 401'd path was successfully retried), conjoined with the fixture
    # matches so a run that made no requests at all cannot bank this.
    # Was `all(dumped[kind] == fixture)`, i.e. the blob compare again, kept alive
    # inside another check's condition. Same guard, stated through the per-field
    # differ: this exists only so a run that made no requests at all cannot bank
    # the reauth check.
    fixtures_ok = not any(
        store_row_diff(dumped[kind], load_fixture(ctx, f"{_FIXTURE_NAME[kind]}_post_cp2.json"))
        for kind in _KINDS
    )
    unresolved_401s = []
    for log in (request_log_cp4, request_log):
        for e in log:
            if status_of(e) != 401:
                continue
            recovered = any(
                o.get("path") == e.get("path")
                and o.get("ts", 0) > e.get("ts", 0)
                and status_of(o) is not None
                and 200 <= status_of(o) < 300
                for o in log
            )
            if not recovered:
                unresolved_401s.append(e.get("path"))
    ctx.check("token_reauth_transparent_under_faults",
        not unresolved_401s and fixtures_ok,
        f"unresolved_401s={unresolved_401s[:4]} fixtures_ok={fixtures_ok}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    await builtin_l2(ctx)

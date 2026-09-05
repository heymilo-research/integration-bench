"""Scenario 3 (L3) -- tamper rejection with exactly-once candidate/application
event application under LIVE duplicate/out-of-order delivery.

Rejecting a mis-signed delivery under a live listener -- while still applying
every genuine candidate/application event exactly once despite TalentLoop's
always-on ~10% duplicate rate and out-of-order shuffle -- is this connector's
core conduct competency, so this is a declared L3 fault-injection scenario.
Seeded tamper injection is always on for TalentLoop (vendor.yaml
webhooks.delivery.tamper_inject: seeded; this task's compose sets
TAMPER_INJECT=1 defensively since the shipped image gates it on that env var).

Flow:
  1. Backfill all 4 entities at checkpoint 0.
  2. Bring the serve listener up, then step the vendor through checkpoints 1,
     4, and 5 ONE AT A TIME (never jump straight to 5 -- see
     _scenario_util.drain_checkpoint_events for why: the dispatcher's queue
     and the delivery log are both scoped to a single boot, and job/note
     mutations at checkpoints 2/3 emit nothing at all). Each of the three
     boots pushes that checkpoint's one genuine candidate/application event
     PLUS its own seeded tampered delivery (deliberately invalid signature,
     stale timestamp) -- draining after each step before the next recreate
     wipes the log.
  3. Drain each step until its genuine event is acked (2xx) and that step's
     tampered delivery has been REJECTED (non-2xx), concatenating every
     step's delivery-log entries for the L3 checks below.
  4. Assert (L3):
       - a tampered delivery was actually sent (across the three boots) and
         NEVER accepted 2xx;
       - the final store matches the post-cp2 answer key for candidates and
         applications (proves the tamper had no effect and dedup/reorder
         handling held).

Then run the built-in L2 hard gates (webhook signature/skew hard gates,
credential hygiene, pagination soft checks).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    VENDOR,
    _is_2xx,
    drain_checkpoint_events,
    dump_store,
    reset_store,
    serve_start,
    serve_stop,
    diff_detail,
    load_fixture,
    row_diff,
)

STEPS = [
    (1, {"evt_00001"}),
    (4, {"evt_00004"}),
    (5, {"evt_00005"}),
]


async def run(ctx) -> None:
    handle = ctx.vendor(VENDOR)

    # -- 1. backfill at cp0 ---------------------------------------------------
    reset_store(ctx)
    handle.recreate(checkpoint=0)
    marker_ts = max((e.get("ts", 0) for e in handle.request_log()), default=-1.0)
    code, _out, err = ctx.app.run(["backfill"])
    # AND-ed with this phase's own data-plane traffic (task-0043 pattern,
    # 2026-08-02): exit 0 alone is vacuously bankable by a do-nothing run, and
    # the store is not dumped in this scenario. Bare "/" healthcheck pings
    # don't count.
    backfill_calls = [
        e for e in handle.request_log()
        if e.get("ts", 0) > marker_ts and e.get("path") not in ("/", "")
    ]
    # Backfill shares poll.py's full-sweep logic (this task's exercise), so
    # this is NOT already satisfied -- still a pure "ran" check, though, so
    # the ran/readable convention applies: pass=0, only a regression costs.
    ctx.check(
        "tamper_backfill_exit_ok",
        code == 0 and len(backfill_calls) > 0,
        f"exit={code} data_plane_calls={len(backfill_calls)} stderr={err[:400]}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # -- 2. cp1 -> cp4 -> cp5, one boot at a time (valid + seeded tampered
    #       delivery per boot) -----------------------------------------------
    serve_start(ctx)
    try:
        drained, deliveries = drain_checkpoint_events(ctx, STEPS)
    finally:
        serve_stop(ctx)

    # Requires the connector's own verify -> dedup -> apply/tombstone logic;
    # an unmodified starter crashes uncaught on every delivery and never acks.
    ctx.check(
        "tamper_drained",
        drained,
        "not all genuine events acked or a tampered delivery not observed rejected",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # L3: a tampered delivery was actually sent (so the gate is really
    # tested). This is a VENDOR-side fact (the dispatcher fires it
    # unconditionally) -- not connector behaviour, so it cannot earn credit;
    # it is a precondition-validity sanity check only.
    tampered = [d for d in deliveries if d.get("tampered")]
    ctx.check(
        "tampered_delivery_present",
        len(tampered) >= 1,
        f"tampered deliveries={len(tampered)}",
        pass_value=0,
        fail_value=0,
        mandatory=False,
    )

    # L3: no tampered delivery, across any of the three boots, was ever
    # accepted (2xx) by the listener.
    # Rejected BY A LIVE LISTENER: a real non-2xx HTTP response, not a
    # connection failure. The dispatcher logs `status_code: None` when nothing
    # answers, so "never accepted" alone passes vacuously for a connector that
    # never listens; requiring an actual non-2xx response (and the genuine
    # event's 2xx ack, via `drained`) makes this an achievement, not a
    # prohibition (2026-07-29 probe-inversion WORKLOG entry). Required
    # security conduct, not already satisfied by an unmodified starter.
    tampered_accepted = [d for d in tampered if _is_2xx(d.get("status_code"))]
    tampered_responded = [
        d for d in tampered
        if d.get("status_code") is not None and not _is_2xx(d.get("status_code"))
    ]
    ctx.check(
        "tampered_delivery_rejected",
        len(tampered) >= 1
        and len(tampered_accepted) == 0
        and len(tampered_responded) >= 1
        and drained,
        f"tampered={len(tampered)} accepted={len(tampered_accepted)} "
        f"responded_non2xx={len(tampered_responded)} genuine_acked={drained}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # L3: the seeded duplicate rate/out-of-order shuffle didn't break dedup --
    # store correct, tamper had no effect.
    dumped = dump_store(ctx)
    if dumped is None:
        ctx.check(
            "tamper_store_readable",
            False,
            "dump produced no output",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return
    candidates, jobs, applications, notes = dumped

    # Restores the signal of the deleted `tamper_candidates_match_fixture` /
    # `tamper_applications_match_fixture` blob compares. candidates is +2 and
    # mandatory: `exactly_once_delete_applied` below asserts one row's tombstone,
    # so a tampered delivery that mutated or suppressed any OTHER row -- the
    # actual attack this scenario exists to rule out -- was going ungraded.
    want_c = load_fixture(ctx, "candidates_post_cp2.json")
    cand_diffs = row_diff(candidates, want_c)
    ctx.check(
        "tamper_candidates_rows_exact",
        not cand_diffs,
        diff_detail("candidates@post_cp2", candidates, want_c, cand_diffs),
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    want_a = load_fixture(ctx, "applications_post_cp2.json")
    app_diffs = row_diff(applications, want_a)
    ctx.check(
        "tamper_applications_rows_exact",
        not app_diffs,
        diff_detail("applications@post_cp2", applications, want_a, app_diffs),
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    by_id = {r["source_id"]: r for r in candidates}
    c7 = by_id.get("cand_0007", {})
    ctx.check(
        "exactly_once_delete_applied",
        c7.get("is_deleted") is True,
        f"cand_0007 is_deleted={c7.get('is_deleted')}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    await builtin_l2(ctx)

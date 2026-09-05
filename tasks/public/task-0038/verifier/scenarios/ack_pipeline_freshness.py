"""Scenario 1 -- ordinary ack-pipeline freshness (spec rungs 1, 2, 6).

`TL_ACK_REQUIRED=1` only; no drop fault. Every candidate/application event in
this run has a genuine delivery attempt available to it -- this scenario
proves the ack recipe is basically right (rung 1: a connector that ignores
the ack requirement gets nothing to land at all) and that the ack bookkeeping
itself is well-formed (rung 2), plus that the job/note poll-only path is
completely unaffected by any of this (rung 6).

Flow:
  1. Backfill all 4 entities at checkpoint 0. Assert the cp0 answer key.
  2. Bring the serve listener up FIRST (see `_scenario_util.serve_start`'s
     docstring for why the ordering matters under the ack retry cadence),
     then step the vendor through checkpoints 1, 4, and 5 ONE AT A TIME
     (index0 cand_0007 delete, index1 job_0003 update, index2 note_0004
     update, index3 cand_0055 update, index4 app_0009 delete -- job/note
     mutations at checkpoints 2/3 emit nothing, so those steps are skipped).
     Each boot also queues its own seeded tampered delivery.
  3. Drain each step for its expected event id, ACKED (not merely 2xx), with
     tampered-rejection required at each.
  4. Run a job/note-only poll pass (the connector's `poll` command never
     touches candidate/application -- see `poll.py`) and assert jobs/notes
     now match the post-milestone answer key, discovered purely through
     polling, independent of anything webhook-related in this scenario.
  5. Assert candidates/applications also now match the post-milestone answer
     key (the webhook path's contribution, since `poll` never touched them).
  6. Check the delivery log's own ack bookkeeping is well-formed (rung 2).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    VENDOR,
    ack_bookkeeping_well_formed_by_event,
    drain_checkpoint_events,
    dump_store,
    load_fixture,
    reset_store,
    serve_start,
    serve_stop,
    set_fault_env,
    grade_cp0_backfill,
)

# Full timeline: index0=cand_0007 delete, index1=job_0003 update (no event),
# index2=note_0004 update (no event), index3=cand_0055 update, index4=app_0009
# delete. checkpoint=N delivers ONLY index N-1's event on that boot.
STEPS = [
    (1, {"evt_00001"}),
    (4, {"evt_00004"}),
    (5, {"evt_00005"}),
]

# checkpoint -> (its one canonical event_id, a human label) -- used to split
# the per-checkpoint ack/bookkeeping checks below so a connector that acks
# SOME of this run's events but not others cannot hide behind a single
# aggregate AND across every checkpoint (each checkpoint here delivers
# exactly one canonical event, so "per checkpoint" and "per event_id"
# coincide).
_STEP_EVENT = {1: ("evt_00001", "cand_0007_delete"), 4: ("evt_00004", "cand_0055_update"),
               5: ("evt_00005", "app_0009_delete")}


async def run(ctx) -> None:
    handle = ctx.vendor(VENDOR)

    # -- 0. no drop fault in this scenario ------------------------------------
    set_fault_env(ctx)

    # -- 1. backfill at cp0 ----------------------------------------------------
    reset_store(ctx)
    handle.recreate(checkpoint=0)

    code, _out, err = ctx.app.run(["backfill"])

    dumped = dump_store(ctx)
    # AND-ed with store readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run.
    ctx.check(
        "backfill_exit_ok",
        code == 0 and dumped is not None,
        f"exit={code} stderr={err[:400]} store_readable={dumped is not None}",
        # backfill is provided/complete plumbing -- any submission already
        # passes this; only a regression should cost.
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
    candidates, jobs, applications, notes = dumped
    grade_cp0_backfill(ctx, candidates, applications)

    # -- 2. serve up FIRST, then step cp1 -> cp4 -> cp5, draining each --------
    serve_start(ctx)
    try:
        per_step, all_deliveries = drain_checkpoint_events(ctx, STEPS)
    finally:
        serve_stop(ctx)

    # Split per checkpoint (was one aggregate AND across all three) -- each
    # checkpoint here delivers exactly one canonical event, so a connector
    # that acks some of this run's events but not others no longer hides
    # behind a single pass/fail.
    for cp, ok in per_step:
        _event_id, label = _STEP_EVENT[cp]
        ctx.check(
            f"webhook_events_acked::{label}",
            ok,
            f"checkpoint {cp} ({label}, {_event_id}): not genuinely acked (or its "
            "tampered delivery not observed rejected)",
            # Rung 1, the floor: the ack recipe is truthfully documented, so a
            # competent from-docs implementation reaches this -- required new
            # behaviour, not itself the mechanic's top-rung subject (that's
            # the DLQ-only recovery in dlq_drain_recovery.py).
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )

    # -- 3. a job/note-only poll pass (candidate/application never touched) ---
    code, _out, err = ctx.app.run(["poll"])

    dumped = dump_store(ctx)
    # AND-ed with store readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run.
    ctx.check(
        "poll_exit_ok",
        code == 0 and dumped is not None,
        f"exit={code} stderr={err[:400]} store_readable={dumped is not None}",
        # poll is provided/complete plumbing -- any submission already passes.
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    if dumped is None:
        ctx.check(
            "freshness_store_readable",
            False,
            "dump produced no output",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return
    candidates, jobs, applications, notes = dumped

    post_milestone_jobs = load_fixture(ctx, "jobs_post_cp2.json")
    post_milestone_notes = load_fixture(ctx, "notes_post_cp2.json")
    ctx.check(
        "job_note_polling_unaffected",
        jobs == post_milestone_jobs and notes == post_milestone_notes,
        f"jobs_rows={len(jobs)} notes_rows={len(notes)} (discovered via poll alone, "
        "no webhook path exists for job/note)",
        # poll.py is unaffected by anything this task's exercise touches --
        # a submission that changes nothing at all already passes.
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # -- 4. rung 1: ordinary candidate/application deliveries landed correctly
    # Split per id (was one aggregate across candidates+applications): the
    # per-id checks pin down each specific mutation this scenario's webhook
    # path is responsible for; the no_regression checks retain the original
    # whole-list assertion (split by entity kind) so a connector that lands
    # the right ids but corrupts unrelated rows is still caught.
    post_milestone_candidates = load_fixture(ctx, "candidates_post_cp2.json")
    post_milestone_applications = load_fixture(ctx, "applications_post_cp2.json")
    candidates_by_id = {r["source_id"]: r for r in candidates}
    applications_by_id = {r["source_id"]: r for r in applications}
    post_candidates_by_id = {r["source_id"]: r for r in post_milestone_candidates}
    post_applications_by_id = {r["source_id"]: r for r in post_milestone_applications}
    for cand_id in ("cand_0007", "cand_0055"):
        ctx.check(
            f"webhook_freshness_baseline::{cand_id}",
            candidates_by_id.get(cand_id) == post_candidates_by_id.get(cand_id),
            f"{cand_id}: got={candidates_by_id.get(cand_id)} "
            f"want={post_candidates_by_id.get(cand_id)}",
            # Supporting evidence that the webhook path landed data -- an
            # implementation that applies data but gets the ack recipe wrong
            # can still pass this, so it's not the ack-recipe trap itself.
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )
    ctx.check(
        "webhook_freshness_baseline::app_0009",
        applications_by_id.get("app_0009") == post_applications_by_id.get("app_0009"),
        f"app_0009: got={applications_by_id.get('app_0009')} "
        f"want={post_applications_by_id.get('app_0009')}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    ctx.check(
        "webhook_freshness_baseline::no_regression_candidates",
        candidates == post_milestone_candidates,
        f"candidates_rows={len(candidates)}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    ctx.check(
        "webhook_freshness_baseline::no_regression_applications",
        applications == post_milestone_applications,
        f"applications_rows={len(applications)}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # -- 5. rung 2: the ack bookkeeping itself is well-formed ------------------
    # Split per event_id (was one aggregate "did ANY event ever reach
    # acked:true") -- a connector that acks some events but silently fails
    # the recipe on others no longer hides behind a single pass/fail.
    per_event = ack_bookkeeping_well_formed_by_event(all_deliveries)
    for cp, (event_id, label) in _STEP_EVENT.items():
        reached_acked, well_formed, detail = per_event.get(
            event_id, (False, True, f"{event_id}: no canonical attempts logged for this event_id")
        )
        ctx.check(
            f"ack_token_recomputed_per_attempt::{label}",
            reached_acked and well_formed,
            detail,
            # Rung 2: bookkeeping well-formedness, secondary to rung 1.
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )

    await builtin_l2(ctx)

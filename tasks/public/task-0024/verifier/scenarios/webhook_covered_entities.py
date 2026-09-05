"""Scenario 1 -- candidate/application changes land via webhook consumption.

Flow:
  1. Backfill all 4 entities at checkpoint 0 (job/note freshness is scenario
     2's focus).
  2. Bring the serve listener up, then step the vendor through checkpoints
     1, 4, and 5 ONE AT A TIME (the spec's "CP2" milestone timeline: index0
     cand_0007 delete, index1 job_0003 update, index2 note_0004 update,
     index3 cand_0055 update, index4 app_0009 delete). The dispatcher only
     ever queues events for the single half-open window ``(checkpoint-1,
     checkpoint]`` on a given boot -- never cumulative -- and each boot
     truncates the delivery log too, so a single jump straight to checkpoint
     5 would only ever deliver evt_00005; walking 1 -> 4 -> 5 (skipping 2/3,
     which fire nothing since job/note mutations never emit events at all)
     is required to observe all three candidate/application events. Each of
     these three boots also queues its own seeded tampered delivery
     (TAMPER_INJECT is always on in this task's compose).
  3. Drain each step for its expected event id (`cand_0007`'s delete =
     evt_00001 at cp1, `cand_0055`'s update = evt_00004 at cp4, `app_0009`'s
     delete = evt_00005 at cp5) with tampered-rejection required at each.
  4. Assert candidates/applications reflect the resulting deletes/updates.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    VENDOR,
    diff_detail,
    drain_checkpoint_events,
    dump_store,
    grade_cp0_backfill,
    load_fixture,
    reset_store,
    row_diff,
    serve_start,
    serve_stop,
)

# Full timeline: index0=cand_0007 delete, index1=job_0003 update (no event),
# index2=note_0004 update (no event), index3=cand_0055 update, index4=app_0009
# delete. Event ids are 1-based on timeline index. checkpoint=N delivers ONLY
# index N-1's event on that boot, so steps 2/3 are skipped entirely (nothing
# to deliver) and 1/4/5 are each their own recreate+drain cycle.
STEPS = [
    (1, {"evt_00001"}),
    (4, {"evt_00004"}),
    (5, {"evt_00005"}),
]


async def run(ctx) -> None:
    handle = ctx.vendor(VENDOR)

    # -- 1. backfill at cp0 --------------------------------------------------
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

    grade_cp0_backfill(ctx, dumped)

    # -- 2. step through cp1 -> cp4 -> cp5, draining each individually ------
    serve_start(ctx)
    try:
        delivered, _all_deliveries = drain_checkpoint_events(ctx, STEPS)
    finally:
        serve_stop(ctx)

    # Requires the connector's own verify -> dedup -> apply/tombstone logic
    # (webhooks.py is entirely IMPLEMENT ME) -- not already satisfied by an
    # unmodified starter.
    ctx.check(
        "webhook_events_delivered",
        delivered,
        "not all candidate/application events were acked 2xx (or tampered delivery not rejected)",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # -- 3. store reflects the webhook-covered changes -----------------------
    dumped = dump_store(ctx)
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

    # Restores the signal of the deleted `freshness_candidates_match_fixture` /
    # `freshness_applications_match_fixture` blob compares. `freshness_candidates
    # _rows_exact` is +2 AND mandatory, which also closes the inverse case: no
    # `poll` runs in this scenario, so candidate/application freshness here can
    # ONLY have come from consuming the webhooks. A connector that rejects every
    # delivery (or never listens) must not be Solved, and without this check it
    # could be — the two mandatory checks in poll_only_entities_recur.py are
    # reachable by a poll-only connector that ignores webhooks entirely.
    want_c = load_fixture(ctx, "candidates_post_cp2.json")
    cand_diffs = row_diff(candidates, want_c)
    ctx.check(
        "freshness_candidates_rows_exact",
        not cand_diffs,
        diff_detail("candidates@post_cp2", candidates, want_c, cand_diffs),
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    want_a = load_fixture(ctx, "applications_post_cp2.json")
    app_diffs = row_diff(applications, want_a)
    ctx.check(
        "freshness_applications_rows_exact",
        not app_diffs,
        diff_detail("applications@post_cp2", applications, want_a, app_diffs),
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # Required correctness of the webhook-covered path -- supporting evidence
    # for "unified freshness," but the task's distinguishing/trap mechanic is
    # job/note RECURRING poll freshness (see poll_only_entities_recur.py); the
    # selective-subscription fact itself (candidate/application-only events)
    # is documented truthfully here, not a lie to trip over.
    by_id = {r["source_id"]: r for r in candidates}
    c7 = by_id.get("cand_0007", {})
    ctx.check(
        "webhook_applied_delete::cand_0007", c7.get("is_deleted") is True,
        f"cand_0007 is_deleted={c7.get('is_deleted')}",
        pass_value=1, fail_value=0, mandatory=False,
    )
    c55 = by_id.get("cand_0055", {})
    ctx.check(
        "webhook_applied_update::cand_0055",
        c55.get("data", {}).get("pipeline_status") == "placed",
        f"cand_0055 pipeline_status={c55.get('data', {}).get('pipeline_status')}",
        pass_value=1, fail_value=0, mandatory=False,
    )
    apps_by_id = {r["source_id"]: r for r in applications}
    a9 = apps_by_id.get("app_0009", {})
    ctx.check(
        "webhook_applied_delete::app_0009", a9.get("is_deleted") is True,
        f"app_0009 is_deleted={a9.get('is_deleted')}",
        pass_value=1, fail_value=0, mandatory=False,
    )

    await builtin_l2(ctx)

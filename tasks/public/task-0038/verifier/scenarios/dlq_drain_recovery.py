"""Scenario 2 (L3) -- the guaranteed-dropped event's dead-letter-only recovery
(spec rungs 3, 4, 5).

Adds `FAULT_DROP_EVENT_IDS=evt_00005` on top of the baseline ack-required
mode: `app_0009`'s delete (index4, the LAST timeline entry) never gets a
single delivery attempt, ever -- it is queued straight into the vendor's
dead-letter queue (with `attempts: 0`) the instant the vendor boots at
checkpoint 5. There is no other path to this mutation: not a webhook
delivery (never attempted), not a polling reconcile (candidate/application
freshness has no poll-side path in this connector -- see `poll.py`). The
only way to observe or recover it is the dead-letter-queue endpoints
themselves.

Flow:
  1. Backfill at checkpoint 0.
  2. Serve up FIRST, then step cp1 -> cp4 (ordinary ack-pipeline deliveries,
     proving the drop fault elsewhere doesn't disturb them).
  3. Recreate at checkpoint 5 (the drop fault is now live for evt_00005).
     Confirm it NEVER receives a delivery attempt, and that the vendor's own
     dead-letter queue -- queried directly by the verifier, independent of
     anything the connector claims -- shows it with `attempts: 0`.
  4. With serve still up (a redrive-based recovery needs a live listener; a
     direct-apply recovery doesn't care either way -- both are legitimate
     per the vendor's documented contract), run the connector's recovery
     pass once.
  5. Assert the underlying mutation (app_0009's delete) is now correctly
     reflected in the canonical store, and that the vendor's dead-letter
     queue is empty -- an authoritative fact checked directly against the
     vendor's own API, not inferred from the connector's output.
  6. (Traffic-conditional, L3) if any event in this run needed more than one
     delivery attempt, the delivery log never shows more attempts than
     `TL_DELIVERY_MAX_ATTEMPTS` for it.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    VENDOR,
    assert_never_delivered,
    drain_checkpoint_events,
    dump_store,
    list_dead_letters,
    load_fixture,
    reset_store,
    serve_start,
    serve_stop,
    set_fault_env,
    wait_for_dead_letter,
)

DROPPED_EVENT_ID = "evt_00005"
MAX_ATTEMPTS = 3

STEPS_BEFORE_DROP = [
    (1, {"evt_00001"}),
    (4, {"evt_00004"}),
]

# checkpoint -> human label for the split per-checkpoint check below (same
# convention as ack_pipeline_freshness.py's _STEP_EVENT).
_STEP_LABEL = {1: "cand_0007_delete", 4: "cand_0055_update"}


async def run(ctx) -> None:
    handle = ctx.vendor(VENDOR)

    # -- 0. this scenario's fault: the LAST timeline event never delivers ----
    set_fault_env(ctx, FAULT_DROP_EVENT_IDS=DROPPED_EVENT_ID)

    # -- 1. backfill at cp0 ----------------------------------------------------
    reset_store(ctx)
    handle.recreate(checkpoint=0)
    marker_ts = max((e.get("ts", 0) for e in handle.request_log()), default=-1.0)
    code, _out, err = ctx.app.run(["backfill"])
    # AND-ed with this phase's own data-plane traffic (task-0043 pattern,
    # 2026-08-02): exit 0 alone is vacuously bankable by a do-nothing run, and
    # the store is not dumped until the very end of this scenario. Bare "/"
    # healthcheck pings don't count.
    backfill_calls = [
        e for e in handle.request_log()
        if e.get("ts", 0) > marker_ts and e.get("path") not in ("/", "")
    ]
    ctx.check(
        "dlq_backfill_exit_ok",
        code == 0 and len(backfill_calls) > 0,
        f"exit={code} data_plane_calls={len(backfill_calls)} stderr={err[:400]}",
        # backfill is provided/complete plumbing -- any submission already
        # passes this; only a regression should cost.
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # -- 2. serve up FIRST, then cp1 -> cp4 (ordinary ack pipeline, unaffected
    #       by the drop fault targeting a LATER, different event) ------------
    serve_start(ctx)
    per_step_before, deliveries_before = drain_checkpoint_events(ctx, STEPS_BEFORE_DROP)
    # Split per checkpoint (was one aggregate AND across both) -- a connector
    # that acks one of these earlier events but not the other no longer hides
    # behind a single pass/fail.
    for cp, ok in per_step_before:
        label = _STEP_LABEL[cp]
        ctx.check(
            f"ordinary_events_unaffected_by_drop_fault::{label}",
            ok,
            f"checkpoint {cp} ({label}): failed to ack even though the drop fault "
            "only targets a later, different event",
            # MANDATORY, and this is the check that closes an inverse-case hole
            # in the task as a whole. The two mandatory checks below
            # (`dead_letter_recovered_via_drain::app_0009`, `dlq_drained_empty`)
            # are both satisfiable by a connector that acks NOTHING: every event
            # would then dead-letter, and the explicit drain would recover them
            # all. Solved without ever having acked a live delivery. This check
            # is measured on events the drop fault does NOT target, so passing it
            # requires the live ack path to actually work.
            pass_value=2,
            fail_value=0,
            mandatory=True,
        )

    # -- 3. cp5: the drop fault is now live for evt_00005 ----------------------
    # NOTE: never_delivered / dlq_item below are pure VENDOR-side facts, driven
    # entirely by FAULT_DROP_EVENT_IDS + TL_ACK_REQUIRED -- no connector code
    # runs between the recreate and these observations, so no connector
    # implementation (empty, stub, or gold) can make either come out
    # differently. These are scenario-setup preconditions, not connector
    # behavior, so they are asserted (crash the scenario loudly if the harness
    # itself is broken) rather than recorded as scored checks -- a scored
    # check here would bank free credit for every submission unconditionally.
    handle.recreate(checkpoint=5)
    never_delivered = assert_never_delivered(ctx, DROPPED_EVENT_ID)
    assert never_delivered, (
        f"harness precondition violated: {DROPPED_EVENT_ID} should never receive "
        "a single delivery attempt under FAULT_DROP_EVENT_IDS"
    )

    dlq_item = wait_for_dead_letter(ctx, DROPPED_EVENT_ID)
    assert dlq_item is not None and dlq_item.get("attempts") == 0, (
        f"harness precondition violated: dropped event should be visible in the "
        f"vendor's own dead-letter queue with attempts:0 -- item={dlq_item}"
    )

    # -- 4. recovery pass (serve stays up: a redrive-based gold needs it; a
    #       direct-apply gold doesn't care either way -- both admitted) ------
    marker_recover = max((e.get("ts", 0) for e in handle.request_log()), default=-1.0)
    code, _out, err = ctx.app.run(["recover_missed_events"])
    # AND-ed with the recovery pass's OWN data-plane traffic (task-0043
    # pattern, 2026-08-02): exit 0 alone is vacuously bankable by a do-nothing
    # run, and the store dump below is inertia (it still reads fine from the
    # earlier phases). Both admitted golds -- redrive and direct-apply -- must
    # talk to the vendor to learn the dropped mutation, so the slice is always
    # non-empty for a real run.
    recover_calls = [
        e for e in handle.request_log()
        if e.get("ts", 0) > marker_recover and e.get("path") not in ("/", "")
    ]
    ctx.check(
        "recover_missed_events_exit_ok",
        code == 0 and len(recover_calls) > 0,
        f"exit={code} data_plane_calls={len(recover_calls)} stderr={err[:400]}",
        # Required for the recovery pass to run at all -- supporting evidence,
        # not itself proof the dropped mutation actually landed (that's
        # dead_letter_recovered_via_drain below). The starter's recover.py
        # raises NotImplementedError, so an unmodified submission fails this.
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    serve_stop(ctx)

    # -- 5. (L3, top rung) the underlying mutation is recovered ---------------
    dumped = dump_store(ctx)
    if dumped is None:
        ctx.check(
            "dlq_store_readable",
            False,
            "dump produced no output",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return
    candidates, jobs, applications, notes = dumped

    post_milestone_candidates = load_fixture(ctx, "candidates_post_cp2.json")
    post_milestone_applications = load_fixture(ctx, "applications_post_cp2.json")
    # Split per id (was one aggregate across candidates+applications): the
    # per-id checks pin down each specific mutation this run is responsible
    # for (two via the ordinary ack pipeline, one via DLQ-only recovery); the
    # no_regression checks retain the original whole-list assertion (split by
    # entity kind) so a connector that lands the right ids but corrupts
    # unrelated rows is still caught.
    candidates_by_id = {r["source_id"]: r for r in candidates}
    applications_by_id = {r["source_id"]: r for r in applications}
    post_candidates_by_id = {r["source_id"]: r for r in post_milestone_candidates}
    post_applications_by_id = {r["source_id"]: r for r in post_milestone_applications}
    for cand_id in ("cand_0007", "cand_0055"):
        ctx.check(
            f"dead_letter_recovered_via_drain::{cand_id}",
            candidates_by_id.get(cand_id) == post_candidates_by_id.get(cand_id),
            f"{cand_id}: got={candidates_by_id.get(cand_id)} "
            f"want={post_candidates_by_id.get(cand_id)}",
            # These two landed via the ordinary ack pipeline earlier in this
            # scenario -- this just confirms the recovery pass didn't disturb
            # them, not the DLQ-only recovery this scenario is actually about.
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )
    ctx.check(
        "dead_letter_recovered_via_drain::app_0009",
        applications_by_id.get("app_0009") == post_applications_by_id.get("app_0009"),
        f"app_0009: got={applications_by_id.get('app_0009')} "
        f"want={post_applications_by_id.get('app_0009')}",
        # The trap: app_0009's delete never received a single delivery
        # attempt -- recoverable ONLY through the dead-letter-queue drain.
        # This is the mechanic's top rung, what the task is about.
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    ctx.check(
        "dead_letter_recovered_via_drain::no_regression_candidates",
        candidates == post_milestone_candidates,
        f"candidates_rows={len(candidates)}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    ctx.check(
        "dead_letter_recovered_via_drain::no_regression_applications",
        applications == post_milestone_applications,
        f"applications_rows={len(applications)}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    apps_by_id = {r["source_id"]: r for r in applications}
    a9 = apps_by_id.get("app_0009", {})
    ctx.check(
        "dead_letter_recovered_delete_applied::app_0009",
        a9.get("is_deleted") is True,
        f"app_0009 is_deleted={a9.get('is_deleted')} (recoverable ONLY via the "
        "dead-letter queue -- it never received a delivery attempt)",
        # Same crux as dead_letter_recovered_via_drain::app_0009, reinforcing
        # angle -- avoid double-gating the identical underlying defect.
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # -- 6. (L1, output check, top rung) the queue itself ends up empty -------
    final_items = list_dead_letters(ctx)
    ctx.check(
        "dlq_drained_empty",
        final_items == [],
        f"remaining items={final_items}",
        # Distinct required outcome from "the mutation landed": a solution
        # that applies the mutation via some indirect path but never calls
        # delete_dead_letter/redrive leaves the queue non-empty. Explicitly
        # part of the Done-when bar ("nothing left outstanding").
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # -- 7. bounded retry respects max attempts -------------------------------
    # This bound is enforced by the VENDOR's own dispatcher (it stops
    # retrying and dead-letters after max_attempts regardless of what the
    # connector does), so no connector implementation can make this come out
    # differently -- a harness precondition, not connector behavior, so it is
    # asserted rather than recorded as a scored check (same reasoning as the
    # never_delivered/dlq_item asserts above).
    all_deliveries = deliveries_before + handle.webhook_deliveries()
    canonical = [d for d in all_deliveries if not d.get("duplicate") and not d.get("tampered")]
    multi_attempt = [d for d in canonical if (d.get("attempt") or 0) > 1]
    if multi_attempt:
        over_budget = [d for d in canonical if (d.get("attempt") or 0) > MAX_ATTEMPTS]
        assert len(over_budget) == 0, (
            f"harness precondition violated: the vendor's own retry cap should "
            f"never be exceeded regardless of connector behavior -- "
            f"over-budget attempts={over_budget[:3]}"
        )

    await builtin_l2(ctx)

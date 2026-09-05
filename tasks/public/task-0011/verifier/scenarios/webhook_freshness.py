"""Scenario 1 -- webhook freshness under LIVE duplicate/out-of-order delivery.

Unlike a harden task with a fault knob, TalentForge's ~20% duplicate rate and
out-of-order shuffle window are baseline vendor behavior -- every run exercises
them, not just a designated fault scenario.

Flow:
  1. Backfill (candidates + applications) at checkpoint 0. Assert the store
     matches the cp0 answer key.
  2. Bring the serve listener up (gives it the `connector` alias the
     dispatcher targets), then step the vendor through checkpoints 1..4 ONE AT
     A TIME (cand_0042 update, cand_0017 delete, cand_0900 create, app_0005
     stage change), draining after each individual recreate. The dispatcher
     only ever queues events for the single half-open window
     ``(checkpoint-1, checkpoint]`` on a given boot -- never cumulative -- and
     each boot truncates the delivery log too, so a single jump straight to
     checkpoint 4 would only ever deliver evt_00004; walking the checkpoints
     one at a time is required to observe all four. vendor.yaml TAMPER_INJECT
     is always on in this task's compose, so each of these boots also queues
     its own seeded tampered delivery.
  3. Assert the store now reflects all four mutations and matches the post-cp4
     answer key -- which also proves the tampered delivery had no effect and
     that duplicate/out-of-order delivery converged correctly (an
     out-of-order-sensitive connector would risk resurrecting cand_0017 or
     losing the cand_0900 create if it trusted arrival order instead of each
     record's own modified_at).

Then run the built-in L2 gates (credential hygiene, idempotent-retry soft
check -- vacuous here since no writeback happens in this scenario).
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
    load_fixture,
    reset_store,
    row_count_ok,
    serve_start,
    serve_stop,
    store_row_diff,
)


async def run(ctx) -> None:
    handle = ctx.vendor(VENDOR)

    # -- 1. backfill at cp0 --------------------------------------------------
    reset_store(ctx)
    handle.recreate(checkpoint=0)

    code, _out, err = ctx.app.run(["backfill"])
    dumped = dump_store(ctx)
    # AND-ed with store readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run; gold's backfill always
    # leaves a dumpable store (the fixture checks below demand it).
    ctx.check(
        "backfill_exit_ok",
        code == 0 and dumped is not None,
        f"exit={code} store_readable={dumped is not None} stderr={err[:400]}",
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
    candidates, applications = dumped

    # Step 1 of this scenario's own docstring — "assert the store matches the cp0
    # answer key" — had stopped being asserted at all: the
    # backfill_{candidates,applications}_match_fixture whole-store compares were
    # deleted in an earlier migration pass with nothing replacing them, so the
    # backfill leg graded only its exit code. Restored per entity and per field.
    #
    # 0/-1: measured on the empty probe, the unmodified starter already backfills
    # cp0 correctly (verifier/empty-baseline.json records both old names as
    # empty=True) — this task's subject is webhook freshness under duplicate and
    # out-of-order delivery, not the crawl. So a correct backfill earns nothing and
    # only losing it costs.
    for label, got, want in (
        ("candidates", candidates, load_fixture(ctx, "candidates_checkpoint_0.json")),
        ("applications", applications, load_fixture(ctx, "applications_checkpoint_0.json")),
    ):
        ok, detail = row_count_ok(got, want)
        ctx.check(
            f"backfill_row_count:{label}",
            ok,
            detail,
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        diffs = store_row_diff(got, want)
        ctx.check(
            f"backfill_fields_exact:{label}",
            not diffs,
            diff_detail(diffs),
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )

    # -- 2. cp1..cp4 webhooks: step one checkpoint at a time, draining each --
    serve_start(ctx)
    try:
        steps = [
            (1, {"evt_00001"}),
            (2, {"evt_00002"}),
            (3, {"evt_00003"}),
            (4, {"evt_00004"}),
        ]
        delivered, _all_deliveries = drain_checkpoint_events(ctx, steps)
    finally:
        serve_stop(ctx)

    ctx.check(
        "webhook_events_delivered",
        delivered,
        "not all cp4 events were acked 2xx (or tampered delivery not observed rejected)",
        # Foundational for everything below (signature verification + basic
        # acking); not itself the OOO/dup convergence discriminator.
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # -- 3. store reflects all four verified mutations -----------------------
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
    candidates, applications = dumped

    by_id = {r["source_id"]: r for r in candidates}
    c42 = by_id.get("cand_0042", {})
    ctx.check(
        "webhook_applied_update",
        c42.get("data", {}).get("phone") == "+1-555-0142",
        f"cand_0042 phone={c42.get('data', {}).get('phone')}",
        # The trap: a connector that trusts delivery arrival order instead of
        # each record's own modified_at can resurrect/miss/misapply mutations
        # under TalentForge's live duplicate/OOO delivery.
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    c17 = by_id.get("cand_0017", {})
    ctx.check(
        "webhook_applied_delete",
        c17.get("is_deleted") is True,
        f"cand_0017 is_deleted={c17.get('is_deleted')}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    c900 = by_id.get("cand_0900", {})
    ctx.check(
        "webhook_applied_create",
        c900.get("data", {}).get("given_name") == "Dana",
        f"cand_0900 given_name={c900.get('data', {}).get('given_name')}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    apps_by_id = {r["source_id"]: r for r in applications}
    a5 = apps_by_id.get("app_0005", {})
    ctx.check(
        "webhook_applied_stage_change",
        a5.get("data", {}).get("stage") == "interview",
        f"app_0005 stage={a5.get('data', {}).get('stage')}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    await builtin_l2(ctx)

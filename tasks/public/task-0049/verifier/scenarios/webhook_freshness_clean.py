"""Scenario 1 — webhook freshness, no faults (sanity gate before faults).

Flow:
  1. Reset the store, recreate the vendor at checkpoint 0 (no faults), bring
     `serve` up. The poll thread's first pass (empty store) does a full
     backfill; assert the store matches the cp0 fixture.
  2. Recreate the vendor at checkpoints 1, 2, 3 in turn. Each recreate's
     webhook dispatcher pushes exactly one signed delivery for that
     checkpoint's newly-applied mutation (evt_00001 placement.updated,
     evt_00002 placement.deleted, evt_00003 placement.created — full event
     coverage, no selective subscription). Wait for each to be delivered,
     then assert the store reflects it.
  3. Final store matches the post-cp3 fixture exactly.

Then run the built-in L2 gates.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    diff_detail,
    dump_placements,
    load_fixture,
    recreate_with_faults,
    reset_store,
    row_diff,
    serve_start,
    serve_stop,
    events_seen,
    wait_for_persisted,
    wait_for_webhook_ids,
)


async def run(ctx) -> None:
    reset_store(ctx)
    recreate_with_faults(ctx, checkpoint=0)
    serve_start(ctx)
    try:
        # Poll thread's first pass is a full backfill from an empty store;
        # give it a moment to complete before inspecting.
        import time

        time.sleep(6.0)
        store = dump_placements(ctx)
        # No-output canon (preserved, per docs/specs/rework/task-0049.spec.md
        # §3 + HARDENING-PATTERNS.md ladder rule): a genuinely empty dump
        # means there is nothing to grade the rest of this scenario or
        # builtin_l2's conduct ladder against, so this returns rather than
        # falling through -- reaching builtin_l2 on a dead run would let its
        # traffic-conditional checks pass vacuously off whatever partial
        # traffic happened before the crash, inflating the floor.
        if store is None:
            ctx.check(
                "cp0_store_readable",
                False,
                "dump produced no output",
                pass_value=0,
                fail_value=-1,
                mandatory=False,
            )
            return
        cp0 = load_fixture(ctx, "placements_cp0.json")
        cp0_diffs = row_diff(store, cp0)
        ctx.check("cp0_backfill_rows_exact",
            not cp0_diffs,
            diff_detail("store@cp0", store, cp0, cp0_diffs),
            pass_value=2,
            fail_value=0,
            mandatory=False,
        )

        checkpoint_events = {
            1: "evt_00001",
            2: "evt_00002",
            3: "evt_00003",
        }
        for cp, event_id in checkpoint_events.items():
            recreate_with_faults(ctx, checkpoint=cp)
            delivered = wait_for_webhook_ids(ctx, {event_id})
            ctx.check(f"cp{cp}_webhook_delivered",
                delivered,
                f"{event_id} was never observed delivered",
                pass_value=0,
                fail_value=-1,
                mandatory=False,
            )
            # An ACK is not an APPLY. The connector answers 2xx and then applies
            # and saves, so asserting store state right after the ack races the
            # apply (see wait_for_persisted's docstring for the measurement).
            # Wait for the event to be recorded as processed in the DURABLE store
            # before moving the vendor to the next checkpoint.
            wait_for_persisted(ctx, events_seen(event_id))
    finally:
        serve_stop(ctx)

    store = dump_placements(ctx)
    if store is None:
        ctx.check(
            "cp3_store_readable",
            False,
            "dump produced no output",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return
    cp3 = load_fixture(ctx, "placements_cp3.json")
    cp3_diffs = row_diff(store, cp3)
    ctx.check("webhook_freshness_rows_exact",
        not cp3_diffs,
        diff_detail("store@cp3", store, cp3, cp3_diffs),
        # The clean-path webhook outcome, with no faults armed: three events
        # consumed and applied. Mandatory -- it is also what stops a connector
        # that never consumes a webhook from being Solved via the poll path.
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    by_id = {r["source_id"]: r for r in store}
    p42 = by_id.get("plc_00042", {})
    ctx.check("cp1_update_applied",
        p42.get("data", {}).get("stage") == "interviewing",
        f"plc_00042 stage={p42.get('data', {}).get('stage')}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    p100 = by_id.get("plc_00100", {})
    ctx.check("cp2_delete_applied",
        p100.get("is_deleted") is True,
        f"plc_00100 is_deleted={p100.get('is_deleted')}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    ctx.check("cp3_create_applied",
        "plc_90001" in by_id,
        "plc_90001 (cp3 create) missing from store",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    await builtin_l2(ctx)

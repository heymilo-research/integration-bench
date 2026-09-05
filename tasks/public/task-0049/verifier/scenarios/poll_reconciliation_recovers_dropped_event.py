"""Scenario 2 — a dropped webhook event is recovered via poll alone.

`FAULT_DROP_EVENT_IDS=evt_00002` only (no other faults). Checkpoint 2's
mutation (placement.deleted, plc_00100) is silently never delivered — the
vendor's delivery log will show no attempt for evt_00002 at all. Since
Placemint deletes are flag-mode (`is_deleted: true`, visible in ordinary
LIST/`modified_since` responses — never webhook-only), the connector's
independent poll-reconciliation pass must converge the store to the true
state (including the delete) with ZERO webhook help.

Flow:
  1. Backfill at checkpoint 0 (clean).
  2. Recreate at checkpoint 2 with the drop fault targeting evt_00002. Bring
     `serve` up; the poll-reconciliation loop runs on its own cadence,
     independent of the (in this case, empty) webhook stream.
  3. Assert: evt_00002 was never observed in the vendor's delivery log (the
     drop actually fired — proving this is a real test, not a no-op), AND
     the store still shows plc_00100 deleted, converged via poll alone.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    diff_detail,
    dump_placements,
    load_fixture,
    recreate_with_faults,
    reset_store,
    wait_for_persisted,
    row_diff,
    serve_start,
    serve_stop,
)


async def run(ctx) -> None:
    reset_store(ctx)
    recreate_with_faults(ctx, checkpoint=0)
    serve_start(ctx)
    try:
        time.sleep(6.0)  # initial full backfill settles

        recreate_with_faults(ctx, checkpoint=2, faults={"FAULT_DROP_EVENT_IDS": "evt_00002"})

        # Give the poll-reconciliation loop a couple of cycles to converge
        # WITHOUT any webhook help (POLL_INTERVAL_S=3 in docker-compose.yaml).
        time.sleep(10.0)

        handle = ctx.vendor("placemint")
        deliveries = handle.webhook_deliveries()
        dropped_seen = any(d.get("event_id") == "evt_00002" for d in deliveries)
        # Evidence gate (mirrors builtin_l2's traffic-conditional rule): the
        # delivery log CANNOT be the gate here — under gold it is legitimately
        # empty (this boot's webhook plan only covers cp2's mutation, which is
        # exactly the dropped event). Gate instead on the connector's own
        # data-plane traffic since the cp2 recreate (gold's poll loop is
        # always polling during the settle window; the compose healthcheck's
        # bare "/" pings don't count) — a do-nothing run produces none and
        # must not bank this on silence.
        data_plane = [e for e in handle.request_log() if e.get("path") not in ("/", "")]
        if data_plane:
            ctx.check("drop_fault_actually_fired",
                not dropped_seen,
                "evt_00002 appeared in the delivery log despite FAULT_DROP_EVENT_IDS",
                pass_value=0,
                fail_value=-1,
                mandatory=False,
            )

        # The dropped delete can only ever arrive via the POLL sweep (its webhook
        # was eaten by FAULT_DROP_EVENT_IDS), so there is no event id to wait on
        # -- wait for the row state itself to land in the durable store. Without
        # this the dump races the poll sweep and reads pre-reconcile state.
        wait_for_persisted(
            ctx,
            lambda st: (st.get("placements") or {}).get("plc_00100", {}).get("is_deleted") is True,
        )

        store = dump_placements(ctx)
        # No-output canon (preserved, per docs/specs/rework/task-0049.spec.md
        # §3 + HARDENING-PATTERNS.md ladder rule): nothing downstream --
        # including builtin_l2's conduct ladder -- is worth grading against a
        # dump that produced no output at all; returning here (rather than
        # hoisting builtin_l2 above this guard) keeps a dead run's floor from
        # being inflated by prohibitions that would pass vacuously off
        # whatever partial traffic happened before the failure.
        if store is None:
            ctx.check(
                "reconcile_store_readable",
                False,
                "dump produced no output",
                pass_value=0,
                fail_value=-1,
                mandatory=False,
            )
            return

        by_id = {r["source_id"]: r for r in store}
        p100 = by_id.get("plc_00100", {})
        ctx.check("dropped_delete_reconciled_via_poll",
            p100.get("is_deleted") is True,
            f"plc_00100 is_deleted={p100.get('is_deleted')} (event never delivered; "
            "poll reconciliation must have recovered this independently)",
            pass_value=2,
            fail_value=0,
            mandatory=True,
        )

        # cp1's update (plc_00042 -> interviewing) is also only reachable via
        # poll in this scenario (this boot's webhook plan only ever covers the
        # SINGLE newest mutation, cp2's — see webhooks.py::build_delivery_plan),
        # so it doubles as a second independent proof the poll path is real.
        p42 = by_id.get("plc_00042", {})
        ctx.check("prior_update_also_reconciled_via_poll",
            p42.get("data", {}).get("stage") == "interviewing",
            f"plc_00042 stage={p42.get('data', {}).get('stage')}",
            pass_value=2,
            fail_value=0,
            mandatory=False,
        )

        fixture = load_fixture(ctx, "placements_cp2.json")
        rec_diffs = row_diff(store, fixture)
        ctx.check("reconcile_rows_exact",
            not rec_diffs,
            diff_detail("store@cp2", store, fixture, rec_diffs),
            # The poll pass must recover the DROPPED delete event -- the only
            # path that can, since the drop fault ate its webhook. Mandatory.
            pass_value=2,
            fail_value=0,
            mandatory=True,
        )
    finally:
        serve_stop(ctx)

    await builtin_l2(ctx)

"""reconverge_under_faults — a fresh full crawl on the mutated checkpoint-2
world, with the SAME injected faults as backfill_under_faults (worker-list
page 1 500s twice, plus a 50-req/60s rate limiter). Proves the drift-driven
multi-pass re-crawl-to-convergence still completes correctly around the
faults, on a world where the committed answer key has actually changed
(wkr_0007 update, wkr_0013 delete, wkr_0190 create).

2026-08 re-ladder (docs/specs/rework/task-0028.spec.md) — see
backfill_under_faults.py for rungs 1-4 (unchanged rationale, same shared
helpers). This scenario adds:

  Rung 5/6 -- `checkpoint2_create_landed` / `checkpoint2_delete_tombstoned` /
              `checkpoint2_update_landed` (L1, the mutated-world answer
              key) plus `checkpoint2_multiple_full_passes` (L1, convergence
              evidence that the drift-driven re-crawl still required
              multiple full passes over /v1/workers with faults
              interleaved). These read from `outputs["worker"]` (guarded
              against None, so a missing worker file fails them
              explicitly rather than crashing) and from the same
              `worker_reqs` log slice rung 3 already computed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    check_entity_correctness,
    check_entity_no_duplicate_ids,
    check_exactly_once,
    check_log_forensics,
    full_pass_starts,
    load_fixture,
    read_outputs,
)

_FIXTURES = {
    "worker": "workers_checkpoint_2.json",
    "gig": "gigs_checkpoint_2.json",
    "assignment": "assignments_checkpoint_2.json",
}
_EXPECTED_COUNTS = {"worker": 121, "gig": 12, "assignment": 40}
async def run(ctx) -> None:
    vendor = ctx.vendor("crewcall")
    vendor._stack.vendor_env["FAULT_RATE_LIMIT"] = "1"
    vendor.recreate(checkpoint=2)

    code, _out, err = ctx.app.run(["sync"])
    request_log = vendor.request_log()
    # AND-ed with data-plane traffic (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run. Rung 1 stays reachable
    # without a byte of correct OUTPUT (per the re-ladder), so gate on wire
    # evidence, not output readability.
    data_plane = [e for e in request_log if e.get("path") not in ("/", "")]
    # Pure plumbing ("the run happened and touched the wire"). Rung 1 is
    # deliberately reachable by any polite connector, so it earns nothing and
    # only a regression costs.
    ctx.check("sync_exit_ok",
        code == 0 and len(data_plane) > 0,
        f"exit={code} stderr={err[:400]} data_plane_requests={len(data_plane)}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # ---- rung 1: process + conduct, unconditional -------------------------
    await builtin_l2(ctx)

    # ---- rung 3: log forensics, unconditional, output-independent --------
    worker_reqs = check_log_forensics(ctx, request_log)

    # ---- rungs 2 & 4: per-entity correctness + exactly-once/no-dup --------
    # `read_outputs` returns a per-entity dict of Optional[list]; a missing
    # file for one entity never blocks grading the other two (fix 2).
    outputs = read_outputs(ctx)
    fixtures = {entity: load_fixture(ctx, filename) for entity, filename in _FIXTURES.items()}
    for entity, fixture in fixtures.items():
        check_entity_correctness(ctx, entity, outputs[entity], fixture, _EXPECTED_COUNTS[entity])
    for entity in _FIXTURES:
        check_entity_no_duplicate_ids(ctx, entity, outputs[entity])
    check_exactly_once(ctx, outputs, fixtures)

    # ---- rung 5/6: mutated-world correctness + convergence evidence ------
    workers = outputs["worker"] or []
    by_id = {r["source_id"]: r for r in workers}
    created = by_id.get("wkr_0190")
    ctx.check("checkpoint2_create_landed",
        created is not None and created.get("data", {}).get("first_name") == "Nadia",
        f"wkr_0190={created}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    tombstoned = by_id.get("wkr_0013", {})
    ctx.check("checkpoint2_delete_tombstoned",
        tombstoned.get("is_deleted") is True,
        f"wkr_0013 is_deleted={tombstoned.get('is_deleted')}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    updated = by_id.get("wkr_0007", {}).get("data", {})
    ctx.check("checkpoint2_update_landed",
        updated.get("status") == "on_shift" and updated.get("rating") == 4.9,
        f"wkr_0007 status={updated.get('status')} rating={updated.get('rating')}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # Convergence evidence: the drift schedule re-runs from index 0 on every
    # fresh boot, so this checkpoint also needs multiple full passes even
    # with faults interleaved. Pure request-log evidence (like rung 3), so
    # it too is unconditional -- no dependency on `outputs`.
    passes = full_pass_starts(worker_reqs)
    # The drift recipe still has to run to convergence WITH faults interleaved --
    # this is the composition the task is named for, read off the request log.
    ctx.check("checkpoint2_multiple_full_passes",
        passes >= 2,
        f"only {passes} full pass(es) over /v1/workers at checkpoint 2",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

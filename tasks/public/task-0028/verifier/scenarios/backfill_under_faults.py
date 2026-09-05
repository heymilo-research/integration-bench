"""backfill_under_faults — full workers/gigs/assignments backfill at checkpoint
0, while CrewCall injects two faults on top of the honest drift quirk:

  - FAULT_5XX_ON_PAGE=1:2 (baked into docker-compose.yaml's vendor env):
    worker-list page 1 (offset 10-19) returns 500 its first two hits, then
    serves normally.
  - This scenario additionally turns on FAULT_RATE_LIMIT=1 (a 50-req/60s
    limiter on GET /v1/* returning 429 + Retry-After: 6) via vendor_env at
    recreate, then re-asserts checkpoint 0 so the fault budgets start fresh.

2026-08 re-ladder (docs/specs/rework/task-0028.spec.md): no single check
gates the rest of the scenario's check mass behind it anymore. Rungs:

  Rung 1 -- `sync_exit_ok` (L1) + the builtin_l2 hard/soft conduct ladder,
            called unconditionally right after the app run, regardless of
            exit code or output correctness. Reachable by any connector
            that behaves politely on the wire even if it never produces a
            byte of correct output.
  Rung 2 -- per-entity `{entity}_matches_fixture` / `{entity}_expected_count`
            (L1). workers/gigs/assignments are graded independently: a
            missing/malformed file for one entity fails only that entity's
            own checks, never the other two (fix 2).
  Rung 3 -- `resume_not_restart` / `retry_after_honored` (L3): pure
            request-log forensics that read ONLY vendor.request_log(), so
            they run regardless of whether `sync` produced any readable
            output at all (fix 3).
  Rung 4 -- `{entity}_no_duplicate_ids` (L1, per entity) + `exactly_once`
            (L3, combined across entities): the exactly-once/no-dup story,
            kept distinct from raw matches_fixture -- a connector with the
            right SET of ids but a stale field value fails rung 2 without
            failing this rung, and vice versa (fix 4).
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
    load_fixture,
    read_outputs,
)

_FIXTURES = {
    "worker": "workers_checkpoint_0.json",
    "gig": "gigs_checkpoint_0.json",
    "assignment": "assignments_checkpoint_0.json",
}
_EXPECTED_COUNTS = {"worker": 120, "gig": 12, "assignment": 40}
async def run(ctx) -> None:
    vendor = ctx.vendor("crewcall")
    vendor._stack.vendor_env["FAULT_RATE_LIMIT"] = "1"
    vendor.recreate(checkpoint=0)

    code, _out, err = ctx.app.run(["sync"])
    request_log = vendor.request_log()
    # AND-ed with data-plane traffic (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run. Rung 1 stays reachable
    # without a byte of correct OUTPUT (per the re-ladder above), so gate on
    # wire evidence, not output readability.
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
    check_log_forensics(ctx, request_log)

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

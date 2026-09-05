"""incremental — StaffLine convergence at CHECKPOINT=1.

Recreates the vendor at CHECKPOINT=1 (the scripted mutation timeline applied:
one candidate update, one DELETE, one create, one application stage change), then
runs ``staffline_fullsync sync`` again. Because initial_sync ran first and left
the back-filled store in the shared output volume, this pass must *converge* that
store to upstream reality:

  * cand_0042's phone update lands, and the created cand_0900 appears.
  * cand_0017, deleted upstream, is tombstoned (is_deleted=true, row RETAINED).
    This is the tombstone-only-delete trap: the delete never appears in any list
    response — the only place it surfaces is GET /svc/tombstones. A connector
    that does not sweep the tombstone feed leaves cand_0017 as a live row and
    fails here.
  * applications stay populated, with app_0005's stage change reflected.

Grading note (2026-08-07). Same repair as initial_sync: the two
``incremental_*.json_matches_fixture`` whole-document compares were deleted in
the per-test-scoring migration and nothing replaced them, so a successful run
recorded only ``app_exit_ok`` and the tombstone trap above — the one thing this
scenario exists to measure — went ungraded. Every mutation in the timeline now
has its own named check, plus per-entity row counts and per-field equality.

The tombstone check is the mandatory one. It is the only check in the task that a
connector reading only the list endpoints cannot pass: the delete is invisible
there by construction, so a docs-faithful crawl converges everything else
correctly and still leaves a deleted candidate live in the store.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2
from bench.verifier.io import read_json_output

from _scenario_util import (  # noqa: E402
    assert_no_query_token,
    by_source_id,
    grade_fields,
    row_count_detail,
)


async def run(ctx) -> None:
    # Advance the scripted timeline: recreate the vendor at CHECKPOINT=1.
    handle = ctx.vendor("staffline")
    handle.recreate(checkpoint=1)

    marker_ts = max((e.get("ts", 0) for e in handle.request_log()), default=-1.0)
    exit_code, stdout, stderr = ctx.app.run(["sync"])
    # AND-ed with this run's own data-plane traffic (task-0043 pattern,
    # 2026-08-02): exit 0 alone is vacuously bankable by a do-nothing run, and
    # the three scenarios here share one output dir so a leftover
    # candidates.json is no evidence that THIS run did anything. The compose
    # healthcheck's bare "/" pings don't count.
    ran_data_calls = [
        e for e in handle.request_log()
        if e.get("ts", 0) > marker_ts and e.get("path") not in ("/", "")
    ]
    ctx.check(
        "app_exit_ok",
        exit_code == 0 and len(ran_data_calls) > 0,
        f"exit={exit_code} data_plane_calls={len(ran_data_calls)} stderr={stderr[:500]}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    outputs: dict[str, object] = {}
    for entity in ("candidates", "applications"):
        output = read_json_output(
            ctx.output_dir / f"{entity}.json",
            timeout_s=15.0 if exit_code == 0 else 0.5,
        )
        outputs[entity] = output
        if output is None:
            ctx.check(
                f"incremental_{entity}.json_exists",
                False,
                f"missing or unreadable {entity}.json",
                pass_value=0,
                fail_value=-1,
                mandatory=False,
            )
        fixture = read_json_output(
            ctx.fixtures / f"{entity}_incremental.json", timeout_s=5.0
        ) or []
        ok, detail = row_count_detail(entity, output, fixture)
        # Not mandatory here: the individual mutations below say what converged
        # and what did not, and the candidates count of 151 already follows from
        # cand_0900 appearing while cand_0017 is retained.
        ctx.check(
            f"incremental_row_count:{entity}",
            ok,
            detail,
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )
        grade_fields(ctx, "incremental_", entity, output, fixture)

    cands = by_source_id(outputs.get("candidates"))
    apps = by_source_id(outputs.get("applications"))

    # -- the tombstone-only delete: the trap ---------------------------------
    # Two conditions in one check on purpose. "Retained" without "flagged" is a
    # connector that never saw the delete; "flagged" without "retained" is a
    # connector that hard-deleted the row, which loses the tombstone downstream
    # consumers need. Only both together is the documented behaviour.
    c17 = cands.get("cand_0017")
    ctx.check(
        "incremental_tombstoned_delete_retained:cand_0017",
        isinstance(c17, dict) and c17.get("is_deleted") is True,
        f"cand_0017={'absent (row was hard-deleted)' if c17 is None else c17.get('is_deleted')!r}"
        " — must be RETAINED with is_deleted=true; the delete appears only in "
        "GET /svc/tombstones, never in a list response",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # -- the rest of the timeline, one check per mutation --------------------
    c42 = cands.get("cand_0042")
    ctx.check(
        "incremental_update_landed:cand_0042_phone",
        isinstance(c42, dict) and (c42.get("data") or {}).get("phone") == "+1-555-0142",
        f"cand_0042 phone={(c42 or {}).get('data', {}).get('phone')!r} want='+1-555-0142'",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    c900 = cands.get("cand_0900")
    ctx.check(
        "incremental_create_landed:cand_0900",
        isinstance(c900, dict)
        and not c900.get("is_deleted")
        and (c900.get("data") or {}).get("fname") == "Dana",
        f"cand_0900={'absent' if c900 is None else c900.get('data')}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    a5 = apps.get("app_0005")
    ctx.check(
        "incremental_stage_change_landed:app_0005",
        isinstance(a5, dict) and (a5.get("data") or {}).get("stage") == "interview",
        f"app_0005 stage={(a5 or {}).get('data', {}).get('stage')!r} want='interview'",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    await builtin_l2(ctx)
    assert_no_query_token(ctx)

"""Scenario 3 — idempotent writeback while the token dies and pages 500.

`FAULT_TOKEN_EXPIRY_MIDRUN=1` + `FAULT_5XX_ON_PAGE="0:3"` together. Flow:

  1. Recreate at checkpoint 0 with both faults. Run `writeback` FIRST (before
     any other client talks to this boot) so its OWN token mint is the
     boot's very first — the one `FAULT_TOKEN_EXPIRY_MIDRUN` forces to an
     early TTL — genuinely stressing the writeback path's re-auth-mid-batch
     handling, not some other surface's.
  2. Assert the batch landed correctly (w-1 note create, w-2 placement
     update, w-3 malformed -> 422), matching the fixture.
  3. Bring `serve` up: its poll thread's full backfill now hits
     `FAULT_5XX_ON_PAGE` on page 0 (up to 3 times) — resume, not restart.
     Assert the resulting store reflects BOTH the vendor's seeded state AND
     writeback's own w-2 placement update (writeback ran first, upstream).
  4. Re-run `writeback` again (same live vendor, simulating a connector
     retry): byte-identical result, no duplicate note/placement-update.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    clear_outputs,
    diff_detail,
    dump_placements,
    load_fixture,
    read_output,
    recreate_with_faults,
    ref_diff,
    reset_store,
    serve_start,
    serve_stop,
)

OK_NOTE_PLACEMENT = "plc_00001"
OK_UPDATE_PLACEMENT = "plc_00002"
MALFORMED_NOTE_PLACEMENT = "plc_00003"


async def run(ctx) -> None:
    reset_store(ctx)
    recreate_with_faults(
        ctx,
        checkpoint=0,
        faults={"FAULT_TOKEN_EXPIRY_MIDRUN": "1", "FAULT_5XX_ON_PAGE": "0:3"},
    )
    clear_outputs(ctx)

    # -- 1. writeback first: its token mint is the boot's first ----------
    exit_code, _stdout, stderr = ctx.app.run(["writeback"])

    output = read_output(ctx, "writeback_result.json", exit_code=exit_code)
    # AND-ed with output readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run. clear_outputs() above
    # guarantees this file can only come from THIS run.
    ctx.check("writeback_exit_ok",
        exit_code == 0 and output is not None,
        f"exit={exit_code} output_readable={output is not None} stderr={stderr[:500]}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )
    # No-output canon (preserved across all four scenarios in this task, per
    # docs/specs/rework/task-0049.spec.md §3 + HARDENING-PATTERNS.md ladder
    # rule): this and the other two guards below intentionally stay as early
    # returns rather than being hoisted above -- reaching builtin_l2's
    # traffic-conditional conduct ladder on a run that produced no real
    # output would let it pass vacuously off partial pre-crash traffic,
    # inflating the floor.
    if output is None:
        ctx.check(
            "writeback_output_exists",
            False,
            "missing or unreadable writeback_result.json",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return

    fixture = load_fixture(ctx, "writeback_result.json")
    wb_diffs = ref_diff(output.get("writes", []), fixture.get("writes", []))
    ctx.check("writeback_writes_fields_exact",
        not wb_diffs,
        diff_detail("writes", output.get("writes", []), fixture.get("writes", []), wb_diffs),
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    writes = output.get("writes", [])
    malformed = next((w for w in writes if w["client_ref"] == "w-3"), {})
    err = malformed.get("error", {})
    ctx.check("malformed_item_reported_as_422",
        malformed.get("ok") is False and err.get("status") == 422 and "body" in (err.get("field_errors") or {}),
        f"error={err}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    handle = ctx.vendor("placemint")
    token_log_first = handle.token_log()
    ctx.check("reauth_transparent_under_writeback",
        len(token_log_first) >= 2,
        f"{len(token_log_first)} token mint(s) during the first writeback pass; "
        "FAULT_TOKEN_EXPIRY_MIDRUN forces the first mint to die early, so a "
        "3-item batch should force at least one re-auth",
        pass_value=2,
        fail_value=0,
        mandatory=False,
    )

    # -- 2. bring serve up: backfill rides through FAULT_5XX_ON_PAGE ------
    serve_start(ctx)
    try:
        time.sleep(8.0)
        store = dump_placements(ctx)
    finally:
        serve_stop(ctx)

    if store is None:
        ctx.check(
            "post_writeback_store_readable",
            False,
            "dump produced no output",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return

    by_id = {r["source_id"]: r for r in store}
    p2 = by_id.get(OK_UPDATE_PLACEMENT, {})
    ctx.check("backfill_reflects_prior_writeback",
        p2.get("data", {}).get("stage") == "placed" and p2.get("data", {}).get("fee_amount") == 9000.0,
        f"{OK_UPDATE_PLACEMENT} data={p2.get('data')}",
        pass_value=2,
        fail_value=0,
        mandatory=False,
    )

    log = handle.request_log()
    fault_pages = [e for e in log if e.get("path") == "/api/placements" and e.get("status", 0) >= 500]
    retried_ok = any(
        e.get("path") == "/api/placements" and e.get("status") == 200 and (e.get("query") or {}).get("offset") in (None, "0", 0)
        for e in log
    )
    ctx.check("resume_not_restart_under_pressure",
        len(fault_pages) >= 1 and retried_ok,
        f"forced 500s on /api/placements={len(fault_pages)}; page 0 eventually succeeded={retried_ok}",
        pass_value=2,
        fail_value=0,
        mandatory=False,
    )

    # -- 3. retried writeback: idempotent, no duplicate creates -----------
    exit_code2, _stdout2, stderr2 = ctx.app.run(["writeback"])
    ctx.check(
        "retry_writeback_exit_ok",
        exit_code2 == 0,
        f"exit={exit_code2} stderr={stderr2[:500]}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    output2 = read_output(ctx, "writeback_result.json", exit_code=exit_code2)
    if output2 is None:
        ctx.check(
            "retry_output_readable",
            False,
            "second writeback produced no output",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return

    ctx.check("retry_did_not_create_new_records",
        output2 == fixture,
        "second writeback result must equal the first pass's result (same ids)",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # NOTE: `handle.request_log()` returns the FULL cumulative request log,
    # not a delta since the last read. The vendor's own idempotency store
    # replays the ORIGINAL response verbatim on a keyed retry (same status —
    # see docs/writeback.md "stored result of the original request"), so a
    # correct, honestly-idempotent connector shows the accepted-status entry
    # TWICE in the cumulative log after two pushes. What actually proves no
    # double-write happened is `retry_did_not_create_new_records` above
    # (same ids/fixture after the second push) plus every repeated write
    # being safely keyed, checked next.
    log2 = handle.request_log()
    repeats = [
        e
        for e in log2
        if e.get("method") in ("POST", "PATCH")
        and e.get("path")
        in (
            f"/api/placements/{OK_NOTE_PLACEMENT}/notes",
            f"/api/placements/{OK_UPDATE_PLACEMENT}",
        )
    ]
    unkeyed = [e for e in repeats if not e.get("idempotency_key")]
    ctx.check("retries_carry_idempotency_key",
        len(unkeyed) == 0,
        f"{len(unkeyed)} write(s) without an Idempotency-Key",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    await builtin_l2(ctx)

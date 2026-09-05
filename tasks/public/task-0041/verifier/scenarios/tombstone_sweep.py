"""Scenario 2 — incremental tombstone sweep after mutations (checkpoint 1).

Deliberately continues from ``initial_sync``'s persisted output/watermarks
(no output clearing, no store reset — see task.yaml's scenario-order note)
to exercise the real "backfill already happened, now reconcile" story.

Flow:
  1. Recreate the vendor at checkpoint 1 (applies every mutation with
     ``at <= 150`` from mutations.yaml: 2 employee updates, 3 employee
     deletes, 1 employee create, 1 assignment update, 1 assignment create,
     1 assignment delete) and run ``sync`` again.
  2. Assert the canonical store matches the checkpoint-1 answer key: updates
     applied, creates picked up by the full crawl, and — critically — every
     delete surfaced ONLY via ``listTombstones`` (Paygrade never flags a
     deleted row on the entity lists themselves) with the prior row's data
     retained (a competent tombstone marks deleted-in-place; it doesn't
     erase history).
  3. Run ``sync`` a THIRD time at the same checkpoint (no new mutations) and
     assert it is a clean no-op: same row counts, and the second pass's
     ``listTombstones`` call resumes from the first pass's watermark rather
     than re-scanning from ``since=0`` — proving the tombstone watermark is
     tracked independently of, and never confused with, the entities' own
     ``mod_ms`` clock (the vendor's permanent, undocumented ``deleted_at``
     skew is exactly the trap here: a connector that conflates the two clocks
     either resweeps from scratch every pass or silently stalls).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    diff_detail,
    index_by_id,
    load_fixture,
    read_output,
    row_diff,
)

_DELETED_EMPLOYEES = {"emp_0005", "emp_0022", "emp_0030"}
_DELETED_ASSIGNMENTS = {"asg_0015"}


def _read_tombstone_watermark(ctx) -> int | None:
    """Read the persisted ``tombstone_since_ms`` from ``.sync_state.json`` —
    the connector's own record of the LAST tombstone `deleted_at` it actually
    processed. This is the correct "how far did the sweep get" signal (same
    technique as task-0050's watermark_separation.py); the REQUEST log's
    ``since=`` query param is NOT it — that param is constant across every
    page of one sweep PASS (it's what was sent, not what was learned), so
    ``max(since sent this pass)`` is trivially the watermark the pass
    STARTED from, not the one it produced."""
    path = ctx.output_dir / ".sync_state.json"
    if not path.is_file():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = state.get("tombstone_since_ms")
    return int(value) if value is not None else None


async def run(ctx) -> None:
    handle = ctx.vendor("paygrade")
    handle.recreate(checkpoint=1)

    # Slice by LOG POSITION, not timestamps: paygrade's request-log lines
    # carry no `ts` field, so a `ts > marker` filter is empty for EVERY run,
    # gold included (found 2026-08-03 when gate 1 failed 5/5 on gold). The
    # log is append-only JSONL, so entries after the pre-run length are
    # exactly this pass's own traffic.
    prior_len = len(handle.request_log())
    code, _out, err = ctx.app.run(["sync"])
    # AND-ed with this pass's OWN data-plane traffic (task-0043 pattern,
    # 2026-08-02): exit 0 alone is vacuously bankable by a do-nothing run, and
    # this scenario never clears output/ -- initial_sync.py wrote these same
    # employees/assignments filenames earlier in the grade, so their
    # readability is not evidence THIS run produced them.
    sweep_calls = [
        e for e in handle.request_log()[prior_len:]
        if e.get("path") not in ("/", "")
    ]
    ctx.check(
        "tombstone_sweep_exit_ok",
        code == 0 and len(sweep_calls) > 0,
        f"exit={code} data_plane_calls={len(sweep_calls)} stderr={err[:400]}",
        # sync.py is fully unimplemented in the starter -- a do-nothing
        # submission crashes here.
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    employees = read_output(ctx, "employees.json")
    assignments = read_output(ctx, "assignments.json")
    ctx.check(
        "tombstone_sweep_outputs_readable",
        employees is not None and assignments is not None,
        f"employees={'ok' if employees is not None else 'MISSING'} "
        f"assignments={'ok' if assignments is not None else 'MISSING'}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    if employees is None or assignments is None:
        return

    emp_by_id = index_by_id(employees)
    asg_by_id = index_by_id(assignments)

    # Deletes surfaced ONLY via the tombstone feed, marked in place with
    # last-known data retained (never erased, never re-created as blank).
    bad_deletes = [
        eid for eid in _DELETED_EMPLOYEES
        if not (emp_by_id.get(eid, {}).get("is_deleted") and emp_by_id[eid].get("data"))
    ]
    bad_deletes += [
        aid for aid in _DELETED_ASSIGNMENTS
        if not (asg_by_id.get(aid, {}).get("is_deleted") and asg_by_id[aid].get("data"))
    ]
    # Restores the deleted `tombstone_sweep_{employees,assignments}_match_fixture`
    # compares. This is the pair that mattered most in this task: Paygrade's
    # mechanic is a tombstone-only delete that RETAINS the row's data, so "the
    # right rows exist with the right tombstone flags" (which the per-entity
    # checks below do cover) and "their retained data is correct" are two
    # different claims — and only the first survived the deletion.
    for entity, rows in (("employees", employees), ("assignments", assignments)):
        want = load_fixture(ctx, f"{entity}_checkpoint_1.json")
        diffs = row_diff(rows, want)
        ctx.check(
            f"tombstone_sweep_{entity}_rows_exact",
            not diffs,
            diff_detail(f"{entity}@cp1", rows, want, diffs),
            pass_value=2,
            fail_value=0,
            mandatory=True,
        )

    ctx.check(
        "tombstone_sweep_deletes_marked_with_data_retained",
        not bad_deletes,
        f"bad delete handling for: {bad_deletes}",
        # The trap: "tombstone-only deletes" -- a deleted id never surfaces
        # on the entity lists at all, so a competent-but-naive full crawl
        # alone never marks it deleted, and a naive tombstone application
        # could also erase history instead of marking deleted-in-place.
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # Spot-check the non-delete mutations the full crawl must also pick up.
    ctx.check(
        "tombstone_sweep_update_emp_0010",
        emp_by_id.get("emp_0010", {}).get("data", {}).get("f_name") == "Marguerite",
        f"emp_0010.f_name={emp_by_id.get('emp_0010', {}).get('data', {}).get('f_name')!r}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    ctx.check(
        "tombstone_sweep_update_emp_0040_status",
        emp_by_id.get("emp_0040", {}).get("data", {}).get("status") == "leave",
        f"emp_0040.status={emp_by_id.get('emp_0040', {}).get('data', {}).get('status')!r}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    ctx.check(
        "tombstone_sweep_create_emp_0901",
        "emp_0901" in emp_by_id and not emp_by_id["emp_0901"].get("is_deleted"),
        f"emp_0901 present={('emp_0901' in emp_by_id)}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    ctx.check(
        "tombstone_sweep_update_asg_0010_pay_rate",
        asg_by_id.get("asg_0010", {}).get("data", {}).get("pay_rate") == 3950,
        f"asg_0010.pay_rate={asg_by_id.get('asg_0010', {}).get('data', {}).get('pay_rate')!r}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    ctx.check(
        "tombstone_sweep_create_asg_0900",
        "asg_0900" in asg_by_id and not asg_by_id["asg_0900"].get("is_deleted"),
        f"asg_0900 present={('asg_0900' in asg_by_id)}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # -- 3. re-run at the same checkpoint: clean no-op, watermark advances --
    request_log_before = ctx.vendor("paygrade").request_log()
    first_pass_tombstone_calls = [
        e for e in request_log_before if (e.get("query") or {}).get("method") == "listTombstones"
    ]
    ctx.check(
        "tombstone_sweep_first_pass_made_tombstone_call",
        len(first_pass_tombstone_calls) >= 1,
        f"{len(first_pass_tombstone_calls)} listTombstones call(s) on the first checkpoint-1 pass",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    # The watermark the first pass PRODUCED (max(deleted_at) it actually
    # processed) — read from .sync_state.json, the connector's own record.
    # NOT derived from the request log's `since=` query param: that's what
    # was SENT (constant across every page of one pass), not what the pass
    # learned, so it can never distinguish "processed some tombstones" from
    # "processed none" the way a persisted watermark can.
    first_pass_max_since = _read_tombstone_watermark(ctx) or 0

    # Position-based slice (paygrade log has no `ts` field — see the first
    # pass above).
    prior_len_rerun = len(ctx.vendor("paygrade").request_log())
    code2, _out2, err2 = ctx.app.run(["sync"])
    # AND-ed with the re-run's OWN data-plane traffic (task-0043 pattern,
    # 2026-08-02): exit 0 alone is vacuously bankable by a do-nothing run, and
    # the outputs are byte-identical to the first pass by design (this is the
    # no-op re-run) so they cannot be this pass's evidence.
    # tombstone_sweep_watermark_advances_not_restarts below already requires
    # gold to re-issue listTombstones here, so the slice is always non-empty.
    rerun_calls = [
        e for e in ctx.vendor("paygrade").request_log()[prior_len_rerun:]
        if e.get("path") not in ("/", "")
    ]
    ctx.check(
        "tombstone_sweep_rerun_exit_ok",
        code2 == 0 and len(rerun_calls) > 0,
        f"exit={code2} data_plane_calls={len(rerun_calls)} stderr={err2[:400]}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    employees2 = read_output(ctx, "employees.json")
    assignments2 = read_output(ctx, "assignments.json")
    ctx.check(
        "tombstone_sweep_rerun_is_noop",
        employees2 == employees and assignments2 == assignments,
        "a no-mutation re-run at the same checkpoint changed the canonical store",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    request_log_after = ctx.vendor("paygrade").request_log()
    second_pass_tombstone_calls = [
        e for e in request_log_after if (e.get("query") or {}).get("method") == "listTombstones"
    ][len(first_pass_tombstone_calls):]
    second_pass_since_values = [
        int((e.get("query") or {}).get("since", -1)) for e in second_pass_tombstone_calls
    ]
    # Watermark separation, the crux of this connector: the re-run's
    # listTombstones since= must resume from the tombstone feed's OWN
    # previous max(deleted_at) (a value in the "clock + 90s skew" range,
    # i.e. strictly greater than 0) — NOT restart from since=0, and NOT be
    # some entity mod_ms value smuggled in from the other watermark.
    ctx.check(
        "tombstone_sweep_watermark_advances_not_restarts",
        bool(second_pass_since_values)
        and all(v == first_pass_max_since for v in second_pass_since_values)
        and first_pass_max_since > 0,
        f"first_pass_max_since={first_pass_max_since} second_pass_since_values={second_pass_since_values}",
        # The trap, per this module's docstring: watermark separation is "the
        # crux of this connector" -- the tombstone deleted_at clock runs a
        # permanent, undocumented skew ahead of the entity mod_ms clock, so
        # conflating the two either resweeps from scratch or silently stalls.
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    await builtin_l2(ctx)

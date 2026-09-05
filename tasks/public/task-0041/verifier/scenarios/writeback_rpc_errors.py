"""Scenario 3 — writeback push loop against HTTP-200 JSON-RPC error bodies.

Independent of the two polling scenarios: recreates the vendor at a clean
checkpoint 0 and runs a single ``writeback`` pass over the staged
``input/pending_writes.json`` batch (2 valid writes, 1 invalid — missing the
required ``pay_rate`` on a ``create_assignment``).

This is the connector's other typed doc lie:
  - wrong_status_semantics: docs promise HTTP 400 on a validation failure;
    Paygrade ALWAYS answers 200 with a JSON-RPC ``error`` body, even for a
    rejected write. A connector that branches on HTTP status alone reports
    every rejected write as a success (and never surfaces the ``-32602
    MISSING pay_rate`` reason anywhere).

It also exercises:
  - ``request_id`` sent as a JSON BODY field (not a header) on every write —
    the vendor's only idempotency handle.
  - read-after-write confirmation: a successful write must be independently
    re-fetched (``getAssignment`` / ``getEmployee``), not just trust the
    create/update response's echoed record.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    diff_detail,
    load_fixture,
    read_output,
    ref_diff,
)


async def run(ctx) -> None:
    ctx.vendor("paygrade").recreate(checkpoint=0)

    code, _out, err = ctx.app.run(["writeback"])

    result = read_output(ctx, "writeback_result.json")
    # AND-ed with output readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run. No other scenario in
    # this task writes writeback_result.json, so it is this run's own output.
    ctx.check(
        "writeback_exit_ok",
        code == 0 and result is not None,
        f"exit={code} output_readable={result is not None} stderr={err[:400]}",
        # writeback.py is fully unimplemented in the starter -- a do-nothing
        # submission crashes here.
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )
    ctx.check(
        "writeback_output_readable",
        result is not None,
        "writeback_result.json missing or unparseable",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )
    if result is None:
        return

    writes = {w["client_ref"]: w for w in result.get("writes", [])}

    # wrong_status_semantics: wb-3 (missing pay_rate) must be recorded as a
    # FAILURE with the RPC error surfaced, not silently accepted because HTTP
    # status was 200.
    # Restores the deleted `writeback_result_matches_fixture`, per client_ref and
    # per field. The checks below grade WHICH refs are reported ok vs failed;
    # this grades the recorded CONTENT of each outcome. That distinction is the
    # point of the task: this gateway answers HTTP 200 for a failed write, so the
    # connector's own recorded result is the only place the failure exists at all.
    fixture = load_fixture(ctx, "writeback_result.json")
    write_diffs = ref_diff(result.get("writes", []), fixture.get("writes", []))
    ctx.check(
        "writeback_writes_fields_exact",
        not write_diffs,
        diff_detail("writes", result.get("writes", []), fixture.get("writes", []), write_diffs),
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    ctx.check(
        "writeback_invalid_write_reported_as_failure",
        writes.get("wb-3", {}).get("ok") is False and "error" in writes.get("wb-3", {}),
        f"wb-3={writes.get('wb-3')!r}",
        # The trap (wrong_status_semantics): docs promise HTTP 400 on a
        # validation failure; Paygrade ALWAYS answers 200 with a JSON-RPC
        # error body. A connector that branches on HTTP status alone reports
        # every rejected write as a success.
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    ctx.check(
        "writeback_valid_writes_reported_as_success",
        writes.get("wb-1", {}).get("ok") is True and writes.get("wb-2", {}).get("ok") is True,
        f"wb-1={writes.get('wb-1')!r} wb-2={writes.get('wb-2')!r}",
        # Complementary half of the same trap: over-correcting to report
        # every write as a failure would also be wrong.
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    request_log = ctx.vendor("paygrade").request_log()
    write_calls = [
        e for e in request_log
        if (e.get("query") or {}).get("method") in ("createAssignment", "updateEmployee")
    ]
    ctx.check(
        "writeback_issued_all_three_write_calls",
        len(write_calls) == 3,
        f"{len(write_calls)} write call(s) made; expected 3 (one per staged item)",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # request_id is a BODY field, never a header, on every write.
    missing_request_id = [e for e in write_calls if not (e.get("body") or {}).get("request_id")]
    ctx.check(
        "writeback_every_write_carries_body_request_id",
        not missing_request_id,
        f"{len(missing_request_id)}/{len(write_calls)} write(s) missing a body request_id",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # Successful writes must be independently confirmed via a point-read, not
    # just trust the create/update response's echoed record.
    confirm_calls = [
        e for e in request_log
        if (e.get("query") or {}).get("method") in ("getAssignment", "getEmployee")
    ]
    ctx.check(
        "writeback_confirms_successful_writes_via_get",
        len(confirm_calls) >= 2,
        f"only {len(confirm_calls)} getAssignment/getEmployee confirmation call(s); expected >= 2",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    await builtin_l2(ctx)

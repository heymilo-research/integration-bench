"""Scenario 4 -- the top rung: the faulted write's true outcome must reach
the connector's own output, and the connector must never have leaned on a
same-window LIST call to decide it. Also the one place builtin_l2's generic
conduct gates run -- once per verdict is enough; this surfaces rung 3
(`idempotent_write_retries`, driven by task.yaml's `writeback.idempotency`
block) exactly once rather than once per scenario (calling it in every
scenario was measured to push this task's floor/gold ratio to ~0.87,
against HARDENING-PATTERNS' 0.40 ceiling, by multiplying a pile of generic
checks that can only ever pass for this task's fixed, given, never-touched
transport).

Recreate the vendor at checkpoint 0 (default FAULT_500_AFTER_COMMIT=3), run
one `correct` pass. Both task-specific checks below are traffic-conditional
on the fault actually having fired (a write call came back >=500 in the
request log) -- if it never fired, neither claim (the report reflects "the"
faulted write, or that no LIST was used to confirm it) is evidence of
anything.

  - `committed_500_row_present`: the connector's OWN output for
    TARGET_CANDIDATE_ID says `ok: true, stage: "rejected"` -- not a phantom
    failure (a connector that trusted the 500 literally and gave up would
    report `ok: false` here even though the write landed) -- cross-checked
    against the request log's Idempotency-Key forensics on that candidate's
    event POSTs so a report of a lucky-looking "success" backed by a real
    duplicate write doesn't pass either.
  - `did_not_confirm_by_relisting` (generalizes task-0016's check of the
    same name to a traffic-conditional, window-scoped form): no candidate
    LIST call appears anywhere in the 20-second read-after-write lag window
    immediately following the faulted write. HireWire's LIST is guaranteed
    stale for that whole window (LIES.md) -- unlike task-0016, this
    connector legitimately calls LIST once up front (to discover the
    backlog), so the check cannot be "zero LIST calls, ever"; it is scoped
    to the window where a LIST call could only ever mislead.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    DEFAULT_FAULT_ENV,
    TARGET_CANDIDATE_ID,
    clear_output,
    distinct_write_count,
    event_calls_for,
    faulted_write_calls,
    list_reads_in_fault_window,
    read_output,
    recreate_vendor,
    run_correct,
)


async def run(ctx) -> None:
    # Explicitly back to the DEFAULT single-lie fault: retry_storm_immunity
    # runs before this scenario and its intensified `3:4` override otherwise
    # stays in force on the shared ComposeStack for the rest of the verdict.
    handle = recreate_vendor(ctx, fault_env=DEFAULT_FAULT_ENV, checkpoint=0)
    clear_output(ctx)

    code, _out, err = run_correct(ctx)
    result = read_output(ctx)

    request_log = handle.request_log()
    faulted = faulted_write_calls(request_log)

    if faulted:
        by_id = {c.get("candidate_id"): c for c in (result or {}).get("corrections", [])}
        entry = by_id.get(TARGET_CANDIDATE_ID, {})
        output_claims_ok = entry.get("ok") is True and entry.get("stage") == "rejected"

        events = sorted(event_calls_for(request_log, TARGET_CANDIDATE_ID), key=lambda e: e.get("ts", 0))
        distinct_events = distinct_write_count(events)

        ctx.check("committed_500_row_present",
            code == 0 and result is not None and output_claims_ok and distinct_events == 1,
            f"correct exit={code} (stderr={err[:200]}); {len(faulted)} faulted write(s) observed; "
            f"output entry for {TARGET_CANDIDATE_ID}={entry!r}; distinct event writes={distinct_events} "
            "(want exactly 1 -- a phantom failure or a real duplicate both fail this)",
            pass_value=2,
            fail_value=0,
            mandatory=False,
        )

        window_hits = list_reads_in_fault_window(request_log)
        ctx.check("did_not_confirm_by_relisting",
            len(window_hits) == 0,
            f"{len(window_hits)} candidate-LIST read(s) fell inside the 20s read-after-write lag window "
            "immediately following a faulted write -- LIST is guaranteed stale there, so any such call "
            "risks (or reflects) using it as the confirmation signal for that write",
            pass_value=2,
            fail_value=0,
            mandatory=False,
        )

    await builtin_l2(ctx)

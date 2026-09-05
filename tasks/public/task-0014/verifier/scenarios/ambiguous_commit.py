"""Scenario 2 -- the heart of the task: the ONE candidate whose write
FAULT_500_AFTER_COMMIT hits must land exactly once, proven from server-side
ground truth the connector's own self-report cannot fake.

Recreate the vendor at checkpoint 0 (default FAULT_500_AFTER_COMMIT=3),
run one `correct` pass, then check TARGET_CANDIDATE_ID (cand_0020, whose
audit-event POST is write-commit-index 3 at gold's write order) two ways
that never trust the connector's output:

  - GET-by-id (issued directly by the verifier, docs/writeback.md's
    immediately-consistent path) shows `stage == "rejected"` -- the
    correction really landed, not a phantom failure.
  - the request log's Idempotency-Key forensics on that candidate's event
    POSTs show exactly ONE logically distinct write -- not zero (dropped)
    and not two-or-more (a blind, unkeyed retry created a REAL duplicate
    event; HireWire mints a fresh row on every keyless POST, LIES.md).

Both failure directions are graded here: a no-retry connector that gives up
on the 500 fails the GET-by-id half (stage never moves); a blind-retry
connector that resubmits with a fresh Idempotency-Key fails the
distinct-write-count half (two real events).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _scenario_util import (  # noqa: E402
    DEFAULT_FAULT_ENV,
    TARGET_CANDIDATE_ID,
    clear_output,
    distinct_write_count,
    event_calls_for,
    recreate_vendor,
    run_correct,
    vendor_get_candidate,
)


async def run(ctx) -> None:
    handle = recreate_vendor(ctx, fault_env=DEFAULT_FAULT_ENV, checkpoint=0)
    clear_output(ctx)

    code, _out, err = run_correct(ctx)

    request_log = handle.request_log()
    events = sorted(event_calls_for(request_log, TARGET_CANDIDATE_ID), key=lambda e: e.get("ts", 0))
    distinct_events = distinct_write_count(events)

    status, body = vendor_get_candidate(ctx, TARGET_CANDIDATE_ID)
    stage_corrected = status == 200 and body.get("stage") == "rejected"

    ctx.check("ambiguous_commit_exact",
        code == 0 and stage_corrected and distinct_events == 1,
        f"correct exit={code} (stderr={err[:200]}); {TARGET_CANDIDATE_ID}: GET-by-id status={status} "
        f"stage={body.get('stage')!r} (want 'rejected'); event POSTs={len(events)} "
        f"distinct(by Idempotency-Key)={distinct_events} (want exactly 1 -- 0 means dropped, "
        ">1 means a real duplicate event was created)",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

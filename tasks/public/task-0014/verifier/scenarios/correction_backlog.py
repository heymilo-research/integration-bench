"""Scenario 1 -- the cheap floor: the correction backlog is identified and
the unfaulted majority lands.

Recreate the vendor at checkpoint 0 (default env: FAULT_500_AFTER_COMMIT=3),
run one `correct` pass, and check the connector's own output against the
fixture: all 26 backlog candidates present, none extra, each with `ok: true`
and `stage: "rejected"`.

This check is deliberately insensitive to HOW the ambiguous write (the one
FAULT_500_AFTER_COMMIT hits) got resolved -- a connector that blind-retries
it still ends up reporting success here, since its own output has no way to
see the resulting duplicate. That is intentional: this rung is reachable
even by a connector that mishandles the fault, so the floor stays reachable
and dense reward keeps flowing. The faulted candidate's REAL server-side
state (exactly one event, no double-apply) is graded independently in
ambiguous_commit.py, using ground truth the connector's self-report cannot
paper over.

Exact-count: a single structural comparison punishes BOTH failure
directions on the backlog set -- an extra id (a phantom row) AND a missing
id (a dropped candidate) both fail `correction_backlog_identified`.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _scenario_util import (  # noqa: E402
    ALL_TARGET_IDS,
    DEFAULT_FAULT_ENV,
    clear_output,
    load_fixture,
    read_output,
    recreate_vendor,
    run_correct,
)


def _contains_required_fields(got, expected) -> bool:
    """Compare the published result fields while allowing diagnostics.

    The task contract requires the candidate id, success verdict and resulting
    stage.  Fields such as ``event_id``, ``action`` or a null ``error`` describe
    the same successful result and must not make it incorrect.
    """
    return isinstance(got, dict) and isinstance(expected, dict) and all(
        got.get(key) == value for key, value in expected.items()
    )


async def run(ctx) -> None:
    recreate_vendor(ctx, fault_env=DEFAULT_FAULT_ENV, checkpoint=0)
    clear_output(ctx)

    code, _out, err = run_correct(ctx)
    result = read_output(ctx)

    if code != 0 or result is None:
        ctx.check("correction_backlog_identified",
            False,
            f"correct exited {code} or produced no readable output; stderr={err[:400]}",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return

    corrections = result.get("corrections", [])
    got_ids = [c.get("candidate_id") for c in corrections]

    fixture = load_fixture(ctx, "writeback_result.json")
    by_id = {c.get("candidate_id"): c for c in corrections}
    fixture_by_id = {c.get("candidate_id"): c for c in fixture.get("corrections", [])}

    # Exact-count: punishes BOTH an extra/duplicate id (more ids than the
    # true backlog, or the same id twice) AND a missing id (fewer than the
    # true backlog) in a single structural comparison.
    exact_set_ok = sorted(got_ids) == ALL_TARGET_IDS and len(got_ids) == len(set(got_ids)) == len(ALL_TARGET_IDS)
    content_mismatches = [
        cid
        for cid in ALL_TARGET_IDS
        if cid in by_id
        and not _contains_required_fields(by_id.get(cid), fixture_by_id.get(cid))
    ]
    content_ok = not content_mismatches

    ctx.check("correction_backlog_identified",
        exact_set_ok and content_ok,
        f"got {len(got_ids)} id(s) (expected {len(ALL_TARGET_IDS)}); "
        f"extra={sorted(set(got_ids) - set(ALL_TARGET_IDS))} "
        f"missing={sorted(set(ALL_TARGET_IDS) - set(got_ids))} "
        f"content_mismatches={content_mismatches}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

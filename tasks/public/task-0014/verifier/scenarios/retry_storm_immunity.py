"""Scenario 3 -- retry-storm immunity.

Under an INTENSIFIED FAULT_500_AFTER_COMMIT (4 consecutive qualifying
commits lie in a row -- write-commit indices 3-6, reaching BOTH the 2nd
(cand_0020) and 3rd (cand_0023) backlog candidates' writes, not just the
single default-config target), a correct connector is IMMUNE: a stable,
per-logical-write Idempotency-Key means every retry only ever replays
HireWire's cached TRUE result (LIES.md: the idempotency cache
short-circuits before the write logic re-runs, so the fault cannot fire
again on that SAME write no matter how large `:times` is) -- whereas a
fresh/mismatched key on the retry makes HireWire treat it as an independent
write, guaranteeing a real duplicate on every single one of the 4 faulted
commits.

Checks:
  - `no_blind_retry_without_key` (L2 hard, traffic-conditional on a retry
    having occurred): every retry of the SAME logical write must carry the
    literal SAME Idempotency-Key as that write's first attempt. Elevates
    conduct-rules.md soft rule 6 to a hard gate for this task, since the
    violation here is not just impolite conduct -- it is the exact
    mechanism the checks below catch in the act.
  - `storm_commit_exact::<candidate_id>` for each storm-affected candidate:
    the SAME server-side-ground-truth check as ambiguous_commit.py
    (GET-by-id stage + distinct event-write count), proving the immunity
    holds for more than one ambiguous commit in the same run.
  - `no_duplicate_events_anywhere` / `no_duplicate_stage_patches_anywhere`:
    the TOTAL count of distinct real writes of each write TYPE across the
    entire 26-candidate backlog is exactly len(ALL_TARGET_IDS) each --
    catches a duplicate landing on ANY candidate, not just the two this
    scenario specifically names, and separates a duplicate EVENT (always a
    genuinely new row) from a duplicate stage PATCH (invisible in the
    final GET-by-id value, since re-applying the same stage converges, but
    still a real extra write against the tenant).
  - `did_not_confirm_by_relisting_under_storm`: no candidate-LIST call
    falls inside the 20s lag window following ANY of the 4 faulted writes.
  - `storm_output_matches_ground_truth`: the connector's OWN output for
    each storm-affected candidate agrees with server-side ground truth. A
    connector whose immediate-retry budget runs out mid-storm (more
    consecutive lies than it's prepared to absorb) and gives up reports a
    phantom failure here -- `ok: false` for a candidate HireWire actually
    committed -- which a single default-fault run (ambiguous_commit.py)
    never exercises, since only one commit lies there.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _scenario_util import (  # noqa: E402
    ALL_TARGET_IDS,
    STORM_AFFECTED_CANDIDATE_IDS,
    STORM_FAULT_ENV,
    TARGET_CANDIDATE_ID,
    clear_output,
    distinct_write_count,
    event_calls_for,
    faulted_write_calls,
    list_reads_in_fault_window,
    patch_calls_for,
    read_output,
    recreate_vendor,
    retried_write_groups,
    run_correct,
    same_key_retry_violations,
    total_distinct_write_count,
    vendor_get_candidate,
)


async def run(ctx) -> None:
    handle = recreate_vendor(ctx, fault_env=STORM_FAULT_ENV, checkpoint=0)
    clear_output(ctx)

    code, _out, err = run_correct(ctx)
    request_log = handle.request_log()

    retried_groups = retried_write_groups(request_log)
    if retried_groups:
        violations = same_key_retry_violations(request_log)
        ctx.check("no_blind_retry_without_key",
            len(violations) == 0,
            f"correct exit={code}; {len(retried_groups)} retried write group(s); "
            f"{len(violations)} retry attempt(s) with no key, or a key that didn't match "
            "the original write's Idempotency-Key",
            pass_value=2,
            fail_value=0,
            mandatory=False,
        )

    # `landed` (>=1 real write -- did it commit AT ALL, regardless of
    # duplication) is deliberately a DIFFERENT question from storm_commit_
    # exact's "==1" (did it commit EXACTLY once) below: this check is only
    # about catching a PHANTOM FAILURE (output says false, but the write
    # genuinely committed) -- a distinct failure mode from a duplicate, and
    # one a single default-fault run (ambiguous_commit.py, only one commit
    # ever lies) can never exercise, since it never exhausts a connector's
    # immediate-retry budget.
    landed: dict[str, bool] = {}
    for candidate_id in STORM_AFFECTED_CANDIDATE_IDS:
        events = sorted(event_calls_for(request_log, candidate_id), key=lambda e: e.get("ts", 0))
        distinct_events = distinct_write_count(events)
        status, body = vendor_get_candidate(ctx, candidate_id)
        stage_corrected = status == 200 and body.get("stage") == "rejected"
        landed[candidate_id] = stage_corrected and distinct_events >= 1
        # Scored per candidate, and NOT uniformly — the two storm-affected
        # candidates have different measured answers and the reason is structural.
        # Under the intensified fault, write-commit indices 3-6 lie, which reaches
        # TARGET_CANDIDATE_ID (cand_0020, the SECOND backlog candidate, whose event
        # POST is index 3 at gold's write order) and cand_0023. Measured on the
        # empty probe: cand_0023 already passes — at the starter's write order its
        # writes do not land on a lying index, so it commits once for free — while
        # cand_0020 fails. Scoring both +1 paid the do-nothing starter 5.0/100 and
        # failed audit bar #5. No name pattern says this; only the probe column
        # does.
        ok = code == 0 and stage_corrected and distinct_events == 1
        detail = (
            f"{candidate_id}: GET-by-id status={status} stage={body.get('stage')!r}; "
            f"distinct event writes={distinct_events} (want exactly 1)"
        )
        if candidate_id == TARGET_CANDIDATE_ID:
            ctx.check(
                f"storm_commit_exact::{candidate_id}",
                ok,
                detail,
                pass_value=1,
                fail_value=0,
                mandatory=False,
            )
        else:
            ctx.check(
                f"storm_commit_exact::{candidate_id}",
                ok,
                detail,
                pass_value=0,
                fail_value=-1,
                mandatory=False,
            )

    result = read_output(ctx)
    by_id = {c.get("candidate_id"): c for c in (result or {}).get("corrections", [])}
    phantom_failures = []
    for candidate_id in STORM_AFFECTED_CANDIDATE_IDS:
        entry = by_id.get(candidate_id, {})
        output_says_ok = entry.get("ok") is True and entry.get("stage") == "rejected"
        if landed.get(candidate_id) and not output_says_ok:
            phantom_failures.append((candidate_id, entry))
    ctx.check("storm_output_matches_ground_truth",
        code == 0 and result is not None and not phantom_failures,
        f"correct exit={code}; candidate(s) HireWire actually committed but the output "
        f"reports as failed (phantom failure)={phantom_failures}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    expected_per_type = len(ALL_TARGET_IDS)
    total_events = total_distinct_write_count(request_log, method="POST")
    ctx.check("no_duplicate_events_anywhere",
        code == 0 and total_events == expected_per_type,
        f"total distinct real audit-event writes observed={total_events} "
        f"(want exactly {expected_per_type} -- more means a duplicate event landed "
        "somewhere in the backlog, not necessarily on a candidate any other check names)",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    total_patches = total_distinct_write_count(request_log, method="PATCH")
    ctx.check("no_duplicate_stage_patches_anywhere",
        code == 0 and total_patches == expected_per_type,
        f"total distinct real stage-PATCH writes observed={total_patches} "
        f"(want exactly {expected_per_type} -- more means a duplicate stage change landed "
        "somewhere, invisible in the final GET-by-id value but still a real extra write)",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # storm_commit_exact's distinct-write count above is event-only (the
    # only entity where a duplicate is a raw, always-countable extra row --
    # LIES.md). A same-value re-PATCH converges to the same final GET-by-id
    # read, so it is invisible there; check it directly on the request log
    # for TARGET_CANDIDATE_ID (gold's write order guarantees its stage
    # PATCH is one of the 4 writes this storm's fault window reaches).
    target_patches = sorted(patch_calls_for(request_log, TARGET_CANDIDATE_ID), key=lambda e: e.get("ts", 0))
    target_patch_distinct = distinct_write_count(target_patches)
    ctx.check("storm_no_stage_patch_duplicate_on_target",
        code == 0 and target_patch_distinct == 1,
        f"{TARGET_CANDIDATE_ID}: distinct stage-PATCH writes under the storm={target_patch_distinct} "
        "(want exactly 1 -- a same-value re-PATCH converges in the final GET-by-id read, invisible "
        "to storm_commit_exact's event-only count, but is still a real extra write)",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # Evidence slice (mirrors builtin_l2's traffic-conditional rule): "no
    # LIST read fell inside a fault window" is only meaningful once a faulted
    # write exists to open a window — an empty request log (do-nothing run)
    # has zero windows and must not bank this prohibition on silence. Gold
    # always trips the storm fault here (STORM_FAULT_ENV targets its own
    # backlog writes), so the slice is always non-empty for a real run.
    if faulted_write_calls(request_log):
        window_hits = list_reads_in_fault_window(request_log)
        ctx.check("did_not_confirm_by_relisting_under_storm",
            len(window_hits) == 0,
            f"{len(window_hits)} candidate-LIST read(s) fell inside the 20s read-after-write lag "
            "window immediately following one of the 4 faulted writes",
            pass_value=2,
            fail_value=0,
            mandatory=False,
        )

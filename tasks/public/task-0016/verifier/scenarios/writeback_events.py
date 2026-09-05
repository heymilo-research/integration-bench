"""Scenario 1 — every staged stage-change event is applied exactly once.

Recreate the vendor at checkpoint 0 (a clean writeback store), run one ``push``,
and prove:

  - the malformed item (missing ``event_type``) is reported as a ``422``
    ``field_errors`` failure — not a crash and not a duplicate;
  - the request log shows exactly one accepted PATCH + one accepted event POST
    per successful logical write (no duplicates); and
  - the connector confirmed via GET-by-id, NOT by re-listing the lagging LIST
    endpoint (the read-after-write trap).

L2 built-in gates run last (credential hygiene, idempotency-on-retry soft check).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    EVENT_REFS,
    FAILED_CANDIDATE_ID,
    FAILED_REFS,
    OK_CANDIDATE_IDS,
    OK_REFS,
    VENDOR,
    candidate_get_by_id_reads,
    candidate_list_reads,
    candidate_patches,
    clear_outputs,
    diff_detail,
    event_field_diff,
    event_posts,
    load_fixture,
    read_writeback_result,
)


async def run(ctx) -> None:
    handle = ctx.vendor(VENDOR)
    handle.recreate(checkpoint=0)
    clear_outputs(ctx)

    code, _out, err = ctx.app.run(["push"])

    result = read_writeback_result(ctx)
    # AND-ed with output readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run. clear_outputs() above
    # guarantees this file can only come from THIS push.
    ctx.check(
        "push_exit_ok",
        code == 0 and result is not None,
        f"exit={code} stderr={err[:400]} output_readable={result is not None}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    if result is None:
        ctx.check(
            "result_readable", False, "push produced no output file",
            pass_value=0, fail_value=-1, mandatory=False,
        )
        return
    ctx.check(
        "result_readable", True, "",
        pass_value=0, fail_value=-1, mandatory=False,
    )

    # Per-ref, per-field agreement with the answer key. Replaces the old
    # `result == fixture` blob compare (`server_state_matches_fixture`): the
    # checks below grade the ref sets, the 422 shape and the request log, but
    # nothing else grades the CONTENTS of each recorded event -- the patched
    # stage, the minted event id, the event_type -- which is exactly what a
    # connector that writes the wrong thing once gets wrong.
    fixture = load_fixture(ctx, "writeback_result.json")
    diffs = event_field_diff(result, fixture)
    for ref in EVENT_REFS:
        ref_diffs = [d for d in diffs if d.get("client_ref") == ref]
        ctx.check(
            f"recorded_event_fields_exact::{ref}",
            not ref_diffs,
            diff_detail(ref, ref_diffs),
            pass_value=1, fail_value=0, mandatory=False,
        )
    unexpected = [d for d in diffs if d.get("field") == "<unexpected event>"]
    ctx.check(
        "no_unexpected_recorded_events",
        not unexpected,
        f"recorded event(s) for client_ref(s) that were never staged: {unexpected}",
        pass_value=1, fail_value=0, mandatory=False,
    )

    events = result.get("events", [])
    got_refs = sorted(e.get("client_ref") for e in events)
    ctx.check(
        "all_event_refs_present_once", got_refs == EVENT_REFS, f"got={got_refs}",
        pass_value=1, fail_value=0, mandatory=False,
    )

    ok_refs = sorted(e["client_ref"] for e in events if e.get("ok") is True)
    failed_refs = sorted(e["client_ref"] for e in events if e.get("ok") is False)
    ctx.check(
        "successful_refs_correct", ok_refs == OK_REFS, f"ok={ok_refs}",
        pass_value=1, fail_value=0, mandatory=False,
    )
    ctx.check(
        "failed_refs_correct", failed_refs == FAILED_REFS, f"failed={failed_refs}",
        pass_value=1, fail_value=0, mandatory=False,
    )

    # The malformed item is reported as a 422 field_errors failure (not a crash,
    # not a silent success).
    failed = next((e for e in events if e["client_ref"] in FAILED_REFS), {})
    err_obj = failed.get("error", {})
    ctx.check(
        "malformed_item_reported_as_422",
        err_obj.get("status") == 422 and "event_type" in (err_obj.get("field_errors") or {}),
        f"error={err_obj}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    request_log = handle.request_log()

    # Exactly one accepted PATCH + one accepted event POST per successful record.
    for ref, cid in OK_CANDIDATE_IDS.items():
        patches = candidate_patches(request_log, candidate_id=cid, accepted_only=True)
        posts = event_posts(request_log, candidate_id=cid, accepted_only=True)
        ctx.check(
            f"one_accepted_patch::{ref}",
            len(patches) == 1,
            f"{cid}: accepted PATCHes={len(patches)} (expected 1)",
            pass_value=1, fail_value=0, mandatory=False,
        )
        ctx.check(
            f"one_accepted_event_post::{ref}",
            len(posts) == 1,
            f"{cid}: accepted event POSTs={len(posts)} (expected 1)",
            pass_value=1, fail_value=0, mandatory=False,
        )

    # The malformed candidate's event POST was rejected (never accepted).
    bad_accepted = event_posts(request_log, candidate_id=FAILED_CANDIDATE_ID, accepted_only=True)
    ctx.check(
        "malformed_event_never_accepted",
        len(bad_accepted) == 0,
        f"{FAILED_CANDIDATE_ID}: accepted malformed POSTs={len(bad_accepted)} (expected 0)",
        pass_value=1, fail_value=0, mandatory=False,
    )

    # Confirmed the right way: at least one GET-by-id per successful candidate, and
    # never a LIST (the lagging read-after-write path).
    for ref, cid in OK_CANDIDATE_IDS.items():
        gets = candidate_get_by_id_reads(request_log, candidate_id=cid)
        ctx.check(
            f"confirmed_via_get_by_id::{ref}",
            len(gets) >= 1,
            f"{cid}: GET-by-id reads={len(gets)} (expected >= 1)",
            pass_value=1, fail_value=0, mandatory=False,
        )
    lists = candidate_list_reads(request_log)
    ctx.check(
        "did_not_confirm_by_relisting",
        len(lists) == 0,
        f"candidate LIST reads={len(lists)} (confirming a fresh write via the lagging LIST is the trap)",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    await builtin_l2(ctx)

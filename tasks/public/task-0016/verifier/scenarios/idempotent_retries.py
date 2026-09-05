"""Scenario 2 — a retried push does not double-post (idempotency).

Production connectors crash and re-run. Recreate the vendor at checkpoint 0,
then run ``push`` TWICE against the same live vendor. A correct connector sends a
stable ``Idempotency-Key`` per logical write, so the second run's PATCH/POST
replay the ORIGINAL records (same server event ids) instead of minting fresh
ones.

Prove:
  - both pushes exit ok;
  - after the second push the recorded event ids are still the SAME as the
    first push (no new records appeared); and
  - every write repeated across the two runs carried an Idempotency-Key (a blind
    un-keyed re-POST is what mints a duplicate event).

A connector that omits the key would, on the second run, POST fresh events
(evt_9000x with NEW ids), so its second-push event ids would differ from the
first push's.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    EVENT_REFS,
    FAILED_CANDIDATE_ID,
    OK_CANDIDATE_IDS,
    VENDOR,
    candidate_patches,
    clear_outputs,
    diff_detail,
    event_field_diff,
    event_posts,
    load_fixture,
    read_writeback_result,
)


def _stable_body(entry) -> str:
    import json

    return json.dumps(entry.get("body"), sort_keys=True, default=str)


async def run(ctx) -> None:
    handle = ctx.vendor(VENDOR)
    handle.recreate(checkpoint=0)
    clear_outputs(ctx)

    # First push.
    code1, _o1, err1 = ctx.app.run(["push"])
    # AND-ed with the first push's own output (task-0043 pattern,
    # 2026-08-02): exit 0 alone is vacuously bankable by a do-nothing run.
    # clear_outputs() above guarantees this file can only come from push 1.
    first_result = read_writeback_result(ctx)
    ctx.check(
        "first_push_exit_ok",
        code1 == 0 and first_result is not None,
        f"exit={code1} stderr={err1[:400]} output_readable={first_result is not None}",
        pass_value=0, fail_value=-1, mandatory=False,
    )

    # Second push against the SAME live vendor (a retry of the whole batch).
    code2, _o2, err2 = ctx.app.run(["push"])

    result = read_writeback_result(ctx)
    # AND-ed with output readability (task-0043 pattern, 2026-08-02).
    ctx.check(
        "second_push_exit_ok",
        code2 == 0 and result is not None,
        f"exit={code2} stderr={err2[:400]} output_readable={result is not None}",
        pass_value=0, fail_value=-1, mandatory=False,
    )

    if result is None:
        ctx.check(
            "result_readable", False, "second push produced no output file",
            pass_value=0, fail_value=-1, mandatory=False,
        )
        return
    ctx.check(
        "result_readable", True, "",
        pass_value=0, fail_value=-1, mandatory=False,
    )

    # After the retry, every recorded field must still equal the answer key --
    # the second push replays, it does not re-mint. Replaces the old
    # `result == fixture` blob compare (`retry_did_not_create_new_records`);
    # event_ids_stable_across_retry below covers only the ids, so a retry that
    # kept the ids but rewrote the candidate's stage (or dropped the 422 item)
    # was going ungraded.
    fixture = load_fixture(ctx, "writeback_result.json")
    diffs = event_field_diff(result, fixture)
    for ref in EVENT_REFS:
        ref_diffs = [d for d in diffs if d.get("client_ref") == ref]
        ctx.check(
            f"retry_recorded_event_fields_unchanged::{ref}",
            not ref_diffs,
            diff_detail(ref, ref_diffs),
            pass_value=1, fail_value=0, mandatory=False,
        )

    request_log = handle.request_log()

    # Across the two runs, the vendor must have accepted at most one PATCH and one
    # event POST per successful candidate (the second run's writes replay via the
    # idempotency key and are NOT counted as fresh accepted creates by the store —
    # the event ids stay evt_90001 / evt_90002).
    ok_event_ids = sorted(
        e["event"]["id"] for e in result.get("events", []) if e.get("ok")
    )
    ctx.check(
        "event_ids_stable_across_retry",
        ok_event_ids == ["evt_90001", "evt_90002"],
        f"event ids={ok_event_ids} (a duplicate post would mint evt_90003+)",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # Every write repeated across the two runs must carry an idempotency key.
    # Group by (method, path, body); any group of size >= 2 whose repeat lacks a
    # key is a blind un-keyed retry (the duplicate-minting bug).
    writes = []
    for cid in OK_CANDIDATE_IDS.values():
        writes += candidate_patches(request_log, candidate_id=cid)
        writes += event_posts(request_log, candidate_id=cid)
    writes += event_posts(request_log, candidate_id=FAILED_CANDIDATE_ID)
    groups: dict[tuple, list] = {}
    for e in writes:
        groups.setdefault((e.get("method"), e.get("path"), _stable_body(e)), []).append(e)
    unkeyed_retries = []
    for entries in groups.values():
        if len(entries) < 2:
            continue
        entries = sorted(entries, key=lambda e: e.get("ts", 0))
        for repeat in entries[1:]:
            if not repeat.get("idempotency_key"):
                unkeyed_retries.append(repeat)
    ctx.check(
        "retries_carry_idempotency_key",
        len(unkeyed_retries) == 0,
        f"{len(unkeyed_retries)} repeated write(s) without an Idempotency-Key",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    await builtin_l2(ctx)

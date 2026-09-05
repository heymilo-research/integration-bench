"""Scenario 2 -- writeback lands exactly once, confirmed the right way.

Recreate the vendor at a clean checkpoint 0, run one ``push`` of the staged
batch in ``repo/input/pending_writeback.json``, and prove:

  - every staged item is reported exactly once (client_ref present once each);
  - the malformed note (empty ``body``) is reported as a `422` `field_errors`
    failure -- not a crash, not a silent success;
  - the recorded server state (note create response; candidate confirmed via
    the immediately-consistent GET-by-id path) matches the fixture EXACTLY;
  - the connector never confirmed a write by re-listing (LIST lags 20s behind
    writes -- the read-after-write trap); and
  - a SECOND push against the same live vendor (crash/retry simulation)
    produces the identical recorded state (same server-assigned ids) and every
    repeated write carried an Idempotency-Key -- no duplicate note/candidate
    landed.

L2 built-in gates run last (credential hygiene, idempotent-retry soft check).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    VENDOR,
    candidate_creates,
    candidate_get_by_id_reads,
    candidate_list_reads,
    candidate_patches,
    clear_outputs,
    diff_detail,
    load_fixture,
    note_posts,
    read_writeback_result,
    reset_store,
    writeback_event_diff,
    writeback_record_ids,
)

OK_REFS = ["wb-001-note", "wb-002-stage", "wb-003-create"]
FAILED_REFS = ["wb-004-bad-note"]
ALL_REFS = sorted(OK_REFS + FAILED_REFS)


def _stable_body(entry) -> str:
    import json

    return json.dumps(entry.get("body"), sort_keys=True, default=str)


async def run(ctx) -> None:
    handle = ctx.vendor(VENDOR)
    reset_store(ctx)
    handle.recreate(checkpoint=0)
    clear_outputs(ctx)

    # -- 1. first push --------------------------------------------------
    code1, _o1, err1 = ctx.app.run(["push"])

    result = read_writeback_result(ctx)
    # AND-ed with output readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run. clear_outputs() above
    # guarantees this file can only come from THIS push.
    ctx.check("first_push_exit_ok",
        code1 == 0 and result is not None,
        f"exit={code1} stderr={err1[:400]} output_readable={result is not None}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    if result is None:
        # Redundant with the second half of first_push_exit_ok, so passing earns
        # nothing; kept because it names the failure on the early-return path.
        ctx.check(
            "result_readable",
            False,
            "push produced no output file",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return
    ctx.check(
        "result_readable",
        True,
        "",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    fixture = load_fixture(ctx, "writeback_result.json")
    # Replaces the deleted `server_state_matches_fixture` whole-document compare.
    # The signal it held was the `record` bodies — the server-assigned ids and
    # values — so this reports differences per client_ref and per field
    # ("wb-003-create.record.id: got='cand_90002' want='cand_90001'") rather than
    # as one zero with "events=4".
    event_diffs = writeback_event_diff(result, fixture)
    ctx.check(
        "writeback_events_match_answer_key",
        not event_diffs,
        diff_detail(event_diffs),
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    events = result.get("events", [])
    got_refs = sorted(e.get("client_ref") for e in events)
    ctx.check(
        "all_refs_present_once",
        got_refs == ALL_REFS,
        f"got={got_refs}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    ok_refs = sorted(e["client_ref"] for e in events if e.get("ok") is True)
    failed_refs = sorted(e["client_ref"] for e in events if e.get("ok") is False)
    ctx.check(
        "successful_refs_correct",
        ok_refs == sorted(OK_REFS),
        f"ok={ok_refs}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    ctx.check(
        "failed_refs_correct",
        failed_refs == sorted(FAILED_REFS),
        f"failed={failed_refs}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    failed = next((e for e in events if e["client_ref"] in FAILED_REFS), {})
    err_obj = failed.get("error", {})
    ctx.check("malformed_note_reported_as_422",
        err_obj.get("status") == 422
        and "body" in (err_obj.get("field_errors") or err_obj.get("errors") or {}),
        f"error={err_obj}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    request_log = handle.request_log()

    # Exactly one accepted write per successful item.
    ctx.check("one_accepted_note_post::wb-001-note",
        len(note_posts(request_log, candidate_id="cand_0010", accepted_only=True)) == 1,
        f"accepted note POSTs={len(note_posts(request_log, candidate_id='cand_0010', accepted_only=True))}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    ctx.check("one_accepted_candidate_patch::wb-002-stage",
        len(candidate_patches(request_log, candidate_id="cand_0020", accepted_only=True)) == 1,
        f"accepted PATCHes={len(candidate_patches(request_log, candidate_id='cand_0020', accepted_only=True))}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    ctx.check("one_accepted_candidate_create::wb-003-create",
        len(candidate_creates(request_log, accepted_only=True)) == 1,
        f"accepted candidate creates={len(candidate_creates(request_log, accepted_only=True))}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    bad_accepted = note_posts(request_log, candidate_id="cand_0030", accepted_only=True)
    ctx.check("malformed_note_never_accepted",
        len(bad_accepted) == 0,
        f"accepted malformed note POSTs={len(bad_accepted)}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # Confirmed the right way: GET-by-id for candidate ops, and NEVER a LIST.
    ctx.check("confirmed_stage_update_via_get_by_id",
        len(candidate_get_by_id_reads(request_log, candidate_id="cand_0020")) >= 1,
        f"GET-by-id reads for cand_0020={len(candidate_get_by_id_reads(request_log, candidate_id='cand_0020'))}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    created_id = None
    ok_create = next((e for e in events if e["client_ref"] == "wb-003-create"), None)
    if ok_create and ok_create.get("ok"):
        created_id = (ok_create.get("record") or {}).get("id") or (ok_create.get("record") or {}).get("source_id")
    ctx.check("confirmed_create_via_get_by_id",
        bool(created_id) and len(candidate_get_by_id_reads(request_log, candidate_id=created_id)) >= 1,
        f"created_id={created_id} GET-by-id reads={len(candidate_get_by_id_reads(request_log, candidate_id=created_id)) if created_id else 0}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    lists = candidate_list_reads(request_log)
    ctx.check("did_not_confirm_by_relisting",
        len(lists) == 0,
        f"candidate LIST reads={len(lists)} (confirming a fresh write via the lagging LIST is the trap)",
        pass_value=2,
        fail_value=0,
        mandatory=False,
    )

    # -- 2. second push (retry/crash simulation) against the SAME vendor -----
    code2, _o2, err2 = ctx.app.run(["push"])

    result2 = read_writeback_result(ctx)
    # AND-ed with output readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run. Deliberately NOT gated
    # on the retry's own vendor traffic: a connector that persists per-item
    # write state may legitimately re-issue nothing on the second push, and
    # this scenario must not require re-sending to score the exit.
    ctx.check("second_push_exit_ok",
        code2 == 0 and result2 is not None,
        f"exit={code2} stderr={err2[:400]} output_readable={result2 is not None}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    if result2 is None:
        ctx.check(
            "second_result_readable",
            False,
            "second push produced no output file",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return
    # Was `result2 == fixture`, a proxy: it fails for any output difference,
    # including ones with nothing to do with idempotency, and its detail string
    # said only "second push server state must equal the first". Now compares the
    # FIRST push's server-assigned ids against the second's, which is the claim the
    # retry leg actually makes. Guarded on the first map being non-empty so a
    # never-wrote-anything connector cannot pass by matching empty against empty.
    ids1, ids2 = writeback_record_ids(result), writeback_record_ids(result2)
    ctx.check("retry_did_not_create_new_records",
        bool(ids1) and ids1 == ids2,
        f"first push ids={ids1 or 'none'} second push ids={ids2 or 'none'}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    # The retry's own output must still agree with the answer key, per field.
    retry_diffs = writeback_event_diff(result2, fixture)
    ctx.check(
        "retry_events_match_answer_key",
        not retry_diffs,
        diff_detail(retry_diffs),
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    request_log2 = handle.request_log()
    # Across BOTH pushes, every accepted write for a given item beyond the
    # FIRST must be an idempotency-key REPLAY of that same original write --
    # never a second, independently-minted record. Note the vendor's
    # documented idempotency semantics (docs/writeback.md): a replayed
    # Idempotency-Key returns the ORIGINAL response VERBATIM, including its
    # original 2xx status -- so a legitimate replay is ALSO counted as
    # "accepted" by a raw status check. Counting raw 2xx entries and
    # demanding exactly one is therefore wrong by construction once a retry
    # is expected to happen (it would only ever pass if the vendor re-created
    # the record, which is precisely the bug this task guards against). The
    # correct assertion is: every accepted write for an item after the first
    # carries the SAME Idempotency-Key as the first (a genuine replay, not a
    # fresh POST/PATCH with a new key), and its response body is byte-
    # identical to the first (the vendor is replaying its cached original
    # response, not minting anything new).
    for label, entries in (
        ("wb-001-note", note_posts(request_log2, candidate_id="cand_0010", accepted_only=True)),
        ("wb-002-stage", candidate_patches(request_log2, candidate_id="cand_0020", accepted_only=True)),
        ("wb-003-create", candidate_creates(request_log2, accepted_only=True)),
    ):
        entries_sorted = sorted(entries, key=lambda e: e.get("ts", 0))
        detail = f"{label}: accepted writes across both pushes={len(entries_sorted)}"
        if not entries_sorted:
            ctx.check(
                f"exactly_one_accepted_write_across_retries::{label}",
                False,
                detail,
                pass_value=2,
                fail_value=0,
                mandatory=True,
            )
            continue
        first = entries_sorted[0]
        replays_ok = True
        for repeat in entries_sorted[1:]:
            same_key = bool(first.get("idempotency_key")) and repeat.get("idempotency_key") == first.get("idempotency_key")
            same_body = repeat.get("body") == first.get("body")
            if not (same_key and same_body):
                replays_ok = False
                detail += f"; non-replay repeat ts={repeat.get('ts')} same_key={same_key} same_body={same_body}"
        ctx.check(f"exactly_one_accepted_write_across_retries::{label}",
            replays_ok,
            detail,
            pass_value=2,
            fail_value=0,
            mandatory=True,
        )

    # Every write repeated across the two runs must carry an Idempotency-Key.
    writes = (
        note_posts(request_log2, candidate_id="cand_0010")
        + candidate_patches(request_log2, candidate_id="cand_0020")
        + candidate_creates(request_log2)
        + note_posts(request_log2, candidate_id="cand_0030")
    )
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
    ctx.check("retries_carry_idempotency_key",
        len(unkeyed_retries) == 0,
        f"{len(unkeyed_retries)} repeated write(s) without an Idempotency-Key",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    await builtin_l2(ctx)

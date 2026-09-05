"""Scenario 3 (L3) -- exactly-once writeback + resilient full-entity sync
under three combined faults: FAULT_5XX_ON_PAGE, FAULT_TOKEN_EXPIRY_MIDRUN,
FAULT_DUP_STORM.

`FAULT_5XX_ON_PAGE="1:2"` forces a 500 on the 0-based page index 1 (offset 50)
of EVERY collection's list endpoint, twice, before relenting -- the
connector's cursor pager must back off and retry the SAME cursor, never
rewind to page 0. `FAULT_TOKEN_EXPIRY_MIDRUN=1` makes the FIRST access token
minted after this vendor boot die after ~8s (every later mint gets the full
TTL) -- a long enough backfill+poll session crosses that boundary and forces
exactly one transparent re-auth; a short one simply never hits it, which is
fine (the check below only requires: no request is ever permanently dropped
because of it, never that it must literally occur every run).
`FAULT_DUP_STORM=1` is armed for defense-in-depth (a webhook listener is not
running in this scenario -- that is scenario 1's job) but must not crash
anything even while armed.

Flow:
  1. Set all three faults; recreate at checkpoint 0.
  2. Run ``backfill`` then ``poll`` (a full session, giving the mid-run token
     death every reasonable chance to actually manifest). Assert both exit 0
     and the resulting store matches the cp0 fixtures for all 4 entities
     EXACTLY -- no duplicate/missing rows despite the mid-page 500s.
  3. From the combined request log:
       - ``resume_not_restart`` -- the faulted ``/candidates`` page is
         retried to 200 on the SAME cursor, and no earlier (page-0) cursor is
         re-fetched afterward.
       - ``token_reauth_transparent`` -- every 401 response in the log is
         followed by a later 200 on that same path (no request is ever left
         permanently failed by the forced early expiry).
  4. Run ``push`` (drains the notes-only batch). Assert the output matches
     the writeback fixture, confirmed via ``GET /notes/{id}`` -- never via
     the lagging ``GET /candidates/{id}/notes`` list.
  5. Run ``push`` again against the SAME vendor (retry/crash simulation).
     Assert byte-identical output (idempotency held even across the fault
     noise), the repeated write carried the SAME Idempotency-Key both times,
     and the vendor never minted a second note id.

L2 built-in gates run last.
"""

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2
from bench.verifier.io import read_json_output

from _scenario_util import (  # noqa: E402
    VENDOR,
    diff_detail,
    dump_store,
    load_fixture,
    reset_store,
    row_count_ok,
    set_fault_env,
    store_row_diff,
    writeback_event_diff,
    writeback_record_ids,
)

_KINDS = ("candidate", "job", "application", "note")
_FIXTURE_NAME = {
    "candidate": "candidates",
    "job": "jobs",
    "application": "applications",
    "note": "notes",
}

_FAULTED_CURSOR_PAGE_OFFSET = 50  # page index 1 * page_size 50


def _stable_body(entry: dict[str, Any]) -> str:
    return json.dumps(entry.get("body"), sort_keys=True, default=str)


def _cursor_of(entry: dict[str, Any]) -> str | None:
    return (entry.get("query") or {}).get("cursor")


def _status_of(entry: dict[str, Any]) -> int:
    try:
        return int(entry.get("status", 0))
    except (TypeError, ValueError):
        return 0


def _candidates_list_reqs(log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        (e for e in log if e.get("method") == "GET" and e.get("path") == "/candidates"),
        key=lambda e: e.get("ts", 0),
    )


def _note_posts(log: list[dict[str, Any]], *, candidate_id: str, accepted_only: bool = False) -> list[dict[str, Any]]:
    path = f"/candidates/{candidate_id}/notes"
    entries = [e for e in log if e.get("method") == "POST" and e.get("path") == path]
    if accepted_only:
        entries = [e for e in entries if _status_of(e) in (200, 201)]
    return entries


def _note_get_by_id_reads(log: list[dict[str, Any]], *, note_id: str) -> list[dict[str, Any]]:
    path = f"/notes/{note_id}"
    return [e for e in log if e.get("method") == "GET" and e.get("path") == path]


def _candidate_notes_list_reads(
    log: list[dict[str, Any]], *, candidate_id: str, since_ts: float = 0.0,
) -> list[dict[str, Any]]:
    """``GET /candidates/{id}/notes`` calls -- the LAGGING list endpoint a
    write-confirmation must never use (``read_after_write_lag_s: 20``).

    ``since_ts`` scopes this to calls at/after the write itself (default 0.0
    keeps the unscoped, whole-log behavior). This is required because
    ``client.py``'s ``iter_notes()`` legitimately pages this SAME endpoint,
    once per candidate, as part of every ordinary ``backfill``/``poll``
    sweep (notes have no top-level list endpoint -- see docs/entities.md);
    those calls happen BEFORE this scenario's push step and are normal sync
    traffic, not a write-confirmation strategy. Passing the push's own POST
    timestamp as ``since_ts`` isolates just the confirmation-relevant calls.
    """
    path = f"/candidates/{candidate_id}/notes"
    return [
        e for e in log
        if e.get("method") == "GET" and e.get("path") == path and e.get("ts", 0) >= since_ts
    ]


def _read_push_output(ctx) -> dict[str, Any] | None:
    return read_json_output(ctx.output_dir / "writeback_result.json", timeout_s=15.0)


async def run(ctx) -> None:
    handle = ctx.vendor(VENDOR)

    reset_store(ctx)
    set_fault_env(
        ctx,
        FAULT_5XX_ON_PAGE="1:2",
        FAULT_TOKEN_EXPIRY_MIDRUN="1",
        FAULT_DUP_STORM="1",
    )
    handle.recreate(checkpoint=0)

    # -- 1/2. a full backfill+poll session under all 3 faults --------------
    # AND-ed with each pass's OWN data-plane traffic (task-0043 pattern,
    # 2026-08-02): exit 0 alone is vacuously bankable by a do-nothing run, and
    # the store is dumped only once after both passes, so it cannot separate
    # them. Bare "/" healthcheck pings don't count.
    marker_backfill = max((e.get("ts", 0) for e in handle.request_log()), default=-1.0)
    code, _out, err = ctx.app.run(["backfill"])
    backfill_calls = [
        e for e in handle.request_log()
        if e.get("ts", 0) > marker_backfill and e.get("path") not in ("/", "")
    ]
    ctx.check("backfill_exit_ok",
        code == 0 and len(backfill_calls) > 0,
        f"exit={code} data_plane_calls={len(backfill_calls)} stderr={err[:400]}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    marker_poll = max((e.get("ts", 0) for e in handle.request_log()), default=-1.0)
    code, _out, err = ctx.app.run(["poll"])
    poll_calls = [
        e for e in handle.request_log()
        if e.get("ts", 0) > marker_poll and e.get("path") not in ("/", "")
    ]
    ctx.check("poll_exit_ok",
        code == 0 and len(poll_calls) > 0,
        f"exit={code} data_plane_calls={len(poll_calls)} stderr={err[:400]}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    dumped = dump_store(ctx)
    if dumped is None:
        ctx.check(
            "session_store_readable",
            False,
            "dump produced no output",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
    else:
        # The store must be byte-identical to cp0 despite the mid-page 500s: no
        # duplicate rows from a pager that rewound, no missing rows from one that
        # gave up. 0/-1 on all four: MEASURED on the empty probe, the starter
        # already survives these faults (all four old names empty=True) — its bug
        # is delete detection, not pagination. The count and the field diff are
        # separated because the two failure modes differ: a rewinding pager
        # duplicates rows (count moves), a truncating one loses them (count moves
        # the other way), and a corrupting one does neither.
        for kind in _KINDS:
            want = load_fixture(ctx, f"{_FIXTURE_NAME[kind]}_checkpoint_0.json")
            ok, detail = row_count_ok(dumped[kind], want)
            # All *_row_count checks in this task are 0/-1 by MEASUREMENT: TalentLoop
            # TOMBSTONES rather than removing rows, and every mutation in this
            # timeline is an update or a tombstone, so the row count is INVARIANT
            # across the whole checkpoint range and the do-nothing starter passes
            # every one of them (measured: empty scored 8.9/100 when they were +1).
            # All the signal lives in the fields_exact check beside each one; the
            # count survives only as a guard against a pager that duplicates or
            # truncates rows.
            ctx.check(
                f"session_row_count:{kind}",
                ok,
                f"{kind}: {detail}",
                pass_value=0,
                fail_value=-1,
                mandatory=False,
            )
            diffs = store_row_diff(dumped[kind], want)
            ctx.check(
                f"session_fields_exact:{kind}",
                not diffs,
                diff_detail(kind, diffs),
                pass_value=0,
                fail_value=-1,
                mandatory=False,
            )

    request_log = handle.request_log()

    # -- 3a. resume_not_restart on /candidates (first collection polled) ---
    cand_reqs = _candidates_list_reqs(request_log)
    fault_entry = next((e for e in cand_reqs if _status_of(e) >= 500), None)
    ctx.check("fault_actually_fired_on_candidates_page",
        fault_entry is not None,
        "expected at least one 5xx on /candidates under FAULT_5XX_ON_PAGE=1:2",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )
    if fault_entry is not None:
        faulted_cursor = _cursor_of(fault_entry)
        fault_ts = fault_entry.get("ts", 0)
        retry_entry = next(
            (e for e in cand_reqs
             if e.get("ts", 0) > fault_ts and _cursor_of(e) == faulted_cursor and _status_of(e) == 200),
            None,
        )
        retried_ok = retry_entry is not None
        # Only the window BETWEEN the fault and its successful same-cursor
        # retry is evidence of "resumed vs. restarted THIS pagination
        # attempt" -- request_log is the WHOLE vendor-process log across
        # BOTH ``backfill`` (where the fault fires) and the SEPARATE, LATER
        # ``poll`` invocation (this task's poll.py does a full fresh sweep
        # every cycle, by design -- see poll.py's module docstring). poll's
        # own first ``/candidates`` page legitimately has no cursor and
        # happens well after the retry succeeded; flagging that as a
        # "restart" would be a false positive against a structurally
        # different, later, unrelated command.
        retry_ts = retry_entry.get("ts", 0) if retry_entry is not None else float("inf")
        restarted = [
            e for e in cand_reqs
            if fault_ts < e.get("ts", 0) < retry_ts and _status_of(e) == 200 and not _cursor_of(e)
        ]
        ctx.check("resume_not_restart",
            retried_ok and not restarted,
            f"retried_same_cursor={retried_ok} spurious_page0_refetches={len(restarted)}",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )

    # -- 3b. token_reauth_transparent: no 401 is ever left unresolved -------
    unresolved_401s = []
    for e in request_log:
        if _status_of(e) != 401:
            continue
        path = e.get("path")
        ts = e.get("ts", 0)
        recovered = any(
            o.get("path") == path and o.get("ts", 0) > ts and _status_of(o) == 200
            for o in request_log
        )
        if not recovered:
            unresolved_401s.append(e)
    ctx.check("token_reauth_transparent",
        len(unresolved_401s) == 0,
        f"unresolved 401s={len(unresolved_401s)} (every expiry must be followed by a successful retry)",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # -- 4. first push -------------------------------------------------------
    code, _out, err = ctx.app.run(["push"])

    result = _read_push_output(ctx)
    # AND-ed with output readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run.
    ctx.check("first_push_exit_ok",
        code == 0 and result is not None,
        f"exit={code} stderr={err[:400]} output_readable={result is not None}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    if result is None:
        ctx.check(
            "push_result_readable",
            False,
            "push produced no output file",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return
    fixture = load_fixture(ctx, "writeback_result.json")
    # Replaces the `result == fixture` whole-document compare, whose detail string
    # said only "events=N". Differences now name the client_ref and the field.
    push_diffs = writeback_event_diff(result, fixture)
    ctx.check(
        "push_events_match_answer_key",
        not push_diffs,
        f"{len(push_diffs)} difference(s): {push_diffs[:4] or 'none'}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    request_log = handle.request_log()
    accepted = _note_posts(request_log, candidate_id="cand_0010", accepted_only=True)
    ctx.check("one_accepted_note_post::wb-001-note",
        len(accepted) == 1,
        f"accepted note POSTs={len(accepted)}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    bad_accepted = _note_posts(request_log, candidate_id="cand_0030", accepted_only=True)
    ctx.check("malformed_note_never_accepted",
        len(bad_accepted) == 0,
        f"accepted malformed note POSTs={len(bad_accepted)}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    created_note_id = None
    ok_event = next((e for e in result.get("events", []) if e.get("client_ref") == "wb-001-note"), None)
    if ok_event and ok_event.get("ok"):
        created_note_id = (ok_event.get("record") or {}).get("id")
    # Scope to calls at/after the accepted write itself: client.py's
    # iter_notes() legitimately pages this SAME per-candidate notes endpoint
    # during the earlier backfill/poll sweep (notes have no top-level list
    # endpoint at all -- see docs/entities.md), so an unscoped whole-log
    # check would false-positive on ordinary sync traffic that has nothing
    # to do with how the write was confirmed.
    write_ts = accepted[0].get("ts", 0) if accepted else 0
    ctx.check("did_not_confirm_by_relisting",
        len(_candidate_notes_list_reads(request_log, candidate_id="cand_0010", since_ts=write_ts)) == 0,
        "GET /candidates/{id}/notes (the lagging LIST) must never be used to confirm a write",
        pass_value=2,
        fail_value=0,
        mandatory=False,
    )
    ctx.check("confirmed_via_get_by_id",
        bool(created_note_id) and len(_note_get_by_id_reads(request_log, note_id=created_note_id)) >= 1,
        f"created_note_id={created_note_id} GET-by-id reads="
        f"{len(_note_get_by_id_reads(request_log, note_id=created_note_id)) if created_note_id else 0}",
        pass_value=2,
        fail_value=0,
        mandatory=False,
    )

    # -- 5. second push (retry/crash simulation) against the SAME vendor ---
    code, _out, err = ctx.app.run(["push"])

    result2 = _read_push_output(ctx)
    # AND-ed with output readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run. Deliberately NOT gated
    # on the retry's own vendor traffic: a connector that persists per-item
    # write state may legitimately re-send nothing on the second push.
    ctx.check("second_push_exit_ok",
        code == 0 and result2 is not None,
        f"exit={code} stderr={err[:400]} output_readable={result2 is not None}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    if result2 is None:
        ctx.check(
            "second_push_result_readable",
            False,
            "second push produced no output file",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return
    # Was `result2 == fixture`, a proxy for idempotency that fails on any output
    # difference. Now states the claim directly: the retry re-attached to the ids
    # the first push created rather than minting new ones. Guarded on the first
    # map being non-empty so a connector that wrote nothing cannot pass by matching
    # empty against empty.
    ids1, ids2 = writeback_record_ids(result), writeback_record_ids(result2)
    ctx.check("exactly_once",
        bool(ids1) and ids1 == ids2,
        f"first push ids={ids1 or 'none'} second push ids={ids2 or 'none'} "
        "(the retry must replay the original records, never mint new ones)",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    # And the retry's own output still agrees with the answer key, per field.
    retry_diffs = writeback_event_diff(result2, fixture)
    ctx.check(
        "retry_events_match_answer_key",
        not retry_diffs,
        f"{len(retry_diffs)} difference(s): {retry_diffs[:4] or 'none'}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # Exactly-once means every retry SAFELY replays the same server-assigned
    # entity, not that the vendor is only ever POSTed to once -- an
    # idempotent create is EXPECTED to accept a 2xx on every retry (that is
    # the entire point of the Idempotency-Key contract: docs/writeback.md's
    # 3600s replay window), so counting raw accepted-POST attempts penalizes
    # a CORRECT retry-safe connector exactly as much as a broken one. The
    # actual invariant -- proven directly by result2 == fixture above, which
    # already asserts the SAME note id/body reappeared -- is restated here
    # from the second event's own record for a standalone, explicit signal.
    second_ok_event = next(
        (e for e in result2.get("events", []) if e.get("client_ref") == "wb-001-note"), None
    )
    second_note_id = (second_ok_event.get("record") or {}).get("id") if second_ok_event else None
    ctx.check("exactly_one_accepted_note_across_both_pushes",
        bool(created_note_id) and second_note_id == created_note_id,
        f"first push note id={created_note_id!r}, second push note id={second_note_id!r} "
        "(expected the SAME id -- the retry must replay the original note, never mint a new one)",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    request_log2 = handle.request_log()
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for e in _note_posts(request_log2, candidate_id="cand_0010"):
        groups.setdefault((e.get("method"), e.get("path"), _stable_body(e)), []).append(e)
    unkeyed_retries = []
    mismatched_keys = []
    for entries in groups.values():
        if len(entries) < 2:
            continue
        entries = sorted(entries, key=lambda e: e.get("ts", 0))
        first_key = entries[0].get("idempotency_key")
        for repeat in entries[1:]:
            if not repeat.get("idempotency_key"):
                unkeyed_retries.append(repeat)
            elif repeat.get("idempotency_key") != first_key:
                mismatched_keys.append(repeat)
    ctx.check("idempotency_key_honored_on_retry",
        len(unkeyed_retries) == 0 and len(mismatched_keys) == 0,
        f"unkeyed_retries={len(unkeyed_retries)} mismatched_keys={len(mismatched_keys)} "
        "(the retry must carry the SAME stable Idempotency-Key as the original attempt)",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    await builtin_l2(ctx)

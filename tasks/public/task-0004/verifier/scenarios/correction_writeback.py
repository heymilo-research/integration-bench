"""correction_writeback -- rung 6 (top rung).

StaffLine's write RPC (``POST /svc/do?action=createNote``) always answers
HTTP 200, whether the write actually landed or not -- the only signal is the
response body's own ``{"ok": ..., "id": ...}`` verdict (see
vendor.staffline.yaml's writeback.validation note). A connector that
confirms success from the status code alone can never report the note's
assigned id at all (it is only ever present in the body) -- so a correctly
populated, fixture-matching ``note_id`` is itself proof the body was read,
not the status.

L1 : merge + correct both exit 0; corrections.json is readable and matches
     the fixture exactly for the 15 disputed candidates (ok=true, non-null
     note_id per StaffLine's actual RPC response) -- scored PER CANDIDATE, so
     partial correctness is measured per row instead of collapsing to one
     bit; cand_0017 (the seeded invalid-ref candidate) never receives a
     correction, since its application was already excluded from the roster
     upstream.
L3 : correction_not_confirmed_by_status -- the fixture-exact match above,
     PLUS a same-target rerun of `correct` that reuses the persisted
     (candidate_id, target_stage) dedupe state: the second invocation posts
     zero additional createNote calls and leaves corrections.json
     byte-identical, proving the write was recorded as done rather than
     re-attempted blindly.
L2 : builtin conduct gates/soft checks against StaffLine -- scored HERE and
     nowhere else. This scenario's traffic is a strict superset of the other
     three (they each drive the same single `merge`; this one drives merge +
     correct + a correct rerun, so it exercises reads and writes), so it is
     the one honest place to score the request-log prohibitions exactly once
     per verdict. See merge_baseline.py's header.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from bench.verifier.builtin_l2 import builtin_l2  # noqa: E402

from _scenario_util import (  # noqa: E402
    DISPUTED,
    INVALID_REF_CANDIDATE_ID,
    STAFFLINE,
    clear_outputs,
    create_note_calls,
    load_fixture,
    read_corrections,
    read_roster,
    recreate_world,
)


async def run(ctx) -> None:
    recreate_world(ctx)
    clear_outputs(ctx)

    merge_code, _out, err = ctx.app.run(["merge"])
    # AND-ed with roster readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run. `merge` always writes
    # output/roster.json under gold (merge_baseline.py fixture-checks it).
    roster = read_roster(ctx, exit_ok=merge_code == 0)
    ctx.check(
        "merge_exit_ok",
        merge_code == 0 and roster is not None,
        f"exit={merge_code} stderr={err[:500]} roster_readable={roster is not None}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    code, _out, err = ctx.app.run(["correct"])

    fixture = load_fixture(ctx, "corrections.json")

    corrections = read_corrections(ctx, exit_ok=merge_code == 0 and code == 0)
    # AND-ed with corrections readability (task-0043 pattern, 2026-08-02):
    # exit 0 alone is vacuously bankable by a do-nothing run.
    ctx.check(
        "correct_exit_ok",
        code == 0 and corrections is not None,
        f"exit={code} stderr={err[:500]} corrections_readable={corrections is not None}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )
    if corrections is None:
        ctx.check(
            "corrections_readable",
            False,
            "missing or unreadable output/corrections.json",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        # Record the full per-candidate check set as failed evidence so the
        # check count stays identical whether or not the connector wrote
        # anything (canon: a no-output path never reaches builtin_l2, but it
        # must not shrink the denominator either).
        for want in fixture:
            ctx.check(
                f"corrections_match_fixture:{want['candidate_id']}",
                False,
                "no corrections.json to check",
                pass_value=1,
                fail_value=0,
                mandatory=False,
            )
        ctx.check(
            # Measured on the empty probe: the given starter ALREADY omits this
            # candidate, so passing earns nothing and only a regression costs.
            "invalid_ref_candidate_never_corrected",
            False,
            "no corrections.json to check",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        ctx.check(
            "correct_rerun_exit_ok",
            False,
            "no corrections.json to check",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        ctx.check(
            "correction_not_confirmed_by_status",
            False,
            "no corrections.json to check",
            pass_value=2,
            fail_value=0,
            mandatory=True,
        )
        return
    ctx.check(
        "corrections_readable",
        True,
        f"rows={len(corrections)}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    by_cand = {c.get("candidate_id"): c for c in corrections if isinstance(c, dict)}

    mismatched = []
    for want in fixture:
        got = by_cand.get(want["candidate_id"])
        if got != want:
            mismatched.append((want["candidate_id"], got))
        differing = (
            "row absent"
            if got is None
            else ", ".join(
                f"{k}={got.get(k)!r} (want {want[k]!r})" for k in want if got.get(k) != want[k]
            )
            or "exact match"
        )
        ctx.check(
            f"corrections_match_fixture:{want['candidate_id']}",
            got == want,
            differing,
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )

    ctx.check(
        "invalid_ref_candidate_never_corrected",
        INVALID_REF_CANDIDATE_ID not in by_cand,
        f"{INVALID_REF_CANDIDATE_ID} present in corrections={INVALID_REF_CANDIDATE_ID in by_cand}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    all_ok_with_note_id = all(
        by_cand.get(d["candidate_id"], {}).get("ok") is True
        and by_cand.get(d["candidate_id"], {}).get("note_id")
        for d in DISPUTED
    )

    # ------------------------------------------------------------- rerun
    sl = ctx.vendor(STAFFLINE)
    first_note_calls = len(create_note_calls(sl.request_log()))

    code2, _out2, err2 = ctx.app.run(["correct"])
    ctx.check(
        "correct_rerun_exit_ok",
        code2 == 0,
        f"exit={code2} stderr={err2[:500]}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    corrections_2 = read_corrections(ctx, exit_ok=code2 == 0)
    unchanged = corrections_2 == corrections

    all_note_calls_after = create_note_calls(sl.request_log())
    no_new_posts = len(all_note_calls_after) == first_note_calls

    # THE TRAP: StaffLine's write RPC always answers HTTP 200 regardless of
    # outcome -- success must be confirmed from the response BODY's own
    # verdict (and note_id, only ever present in the body), never the
    # status code. Compounded with the rerun-idempotency proof so a
    # connector that gets confirmation right but re-posts on every rerun (or
    # vice versa) still fails this.
    ctx.check(
        "correction_not_confirmed_by_status",
        not mismatched and all_ok_with_note_id and unchanged and no_new_posts,
        f"fixture_match={not mismatched} ok_with_note_id={all_ok_with_note_id} "
        f"rerun_unchanged={unchanged} createNote_calls_first_run={first_note_calls} "
        f"total_after_rerun={len(all_note_calls_after)}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    await builtin_l2(ctx)

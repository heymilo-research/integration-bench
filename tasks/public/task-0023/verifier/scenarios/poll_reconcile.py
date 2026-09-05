"""Scenario 2 -- poll-only reconciliation discovers the delete with NO webhook
listener running at all.

This is the scenario that most directly tests whether the connector fell into
the doc's is_deleted-flag trap: it never brings the serve listener up, so the
ONLY way `cand_0007`'s deletion can ever be discovered is the poll path's own
vanish-detection + `GET /candidates/cand_0007` -> 410 confirmation. A
connector that (incorrectly) built its poll sync around a nonexistent
`is_deleted` flag, or that only reacts to webhooks for deletes, fails this
scenario outright -- the row would remain a permanent phantom.

Replaced checks: `poll_backfill_candidates_match_fixture`,
`poll_reconcile_candidates_match_fixture` and
`poll_reconcile_applications_match_fixture` are now the per-row, per-field
`*_rows_exact` diffs below.

Flow:
  1. Backfill at checkpoint 0 (fresh store).
  2. Recreate the vendor at checkpoint 1 (cand_0007 deleted). Do NOT start the
     serve listener -- the vendor's dispatcher will attempt delivery per its
     compose-configured WEBHOOK_TARGET and simply fail to connect (nothing is
     listening at the `connector` alias); that failure is expected and
     harmless.
  3. Run `poll` (one polling pass with the reconcile sweep).
  4. Assert the store reflects `cand_0007` as deleted (both discovery paths --
     webhook and poll-only -- converge on an identical row; see the fixture
     generator's docstring).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    VENDOR,
    diff_detail,
    dump_store,
    load_fixture,
    reset_store,
    row_diff,
)


async def run(ctx) -> None:
    handle = ctx.vendor(VENDOR)

    # -- 1. backfill at cp0 --------------------------------------------------
    reset_store(ctx)
    handle.recreate(checkpoint=0)

    code, _out, err = ctx.app.run(["backfill"])

    dumped = dump_store(ctx)
    # AND-ed with store readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run. Backfill is provided,
    # correct plumbing and no faults are armed here, so an unmodified starter
    # already runs this cleanly.
    ctx.check(
        "poll_backfill_exit_ok",
        code == 0 and dumped is not None,
        f"exit={code} stderr={err[:400]} store_readable={dumped is not None}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    if dumped is None:
        ctx.check(
            "poll_backfill_store_readable",
            False,
            "dump produced no output",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return

    # Restores the signal of the deleted `poll_backfill_candidates_match_fixture`
    # blob compare. 0/-1 by MEASUREMENT: the empty probe passes it.
    cp0_candidates, _cp0_applications = dumped
    want_cp0 = load_fixture(ctx, "candidates_checkpoint_0.json")
    diffs = row_diff(cp0_candidates, want_cp0)
    ctx.check(
        "poll_backfill_candidates_rows_exact",
        not diffs,
        diff_detail("candidates@cp0", cp0_candidates, want_cp0, diffs),
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # -- 2. recreate at cp1 -- NO serve listener is ever started here --------
    handle.recreate(checkpoint=1)

    # -- 3. one polling pass (the exercise: vanish-detection + 410 reconcile) -
    code, _out, err = ctx.app.run(["poll"])

    # -- 4. store reflects the deletion, discovered WITHOUT any webhook -----
    dumped = dump_store(ctx)
    # AND-ed with store readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run. `poll.py`'s reconcile
    # sweep is entirely IMPLEMENT ME (raises NotImplementedError), so an
    # unmodified starter never reaches this state.
    ctx.check(
        "poll_exit_ok",
        code == 0 and dumped is not None,
        f"exit={code} stderr={err[:400]} store_readable={dumped is not None}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    if dumped is None:
        ctx.check(
            "poll_reconcile_store_readable",
            False,
            "dump produced no output",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return
    candidates, applications = dumped

    by_id = {r["source_id"]: r for r in candidates}
    c7 = by_id.get("cand_0007", {})

    # Restores the signal of the deleted `poll_reconcile_candidates_match_fixture` /
    # `poll_reconcile_applications_match_fixture` blob compares, split by entity
    # because the empty-probe column splits by entity: candidates is where the deletion has to be discovered by the poll path's
    # own vanish-detection + 410 confirmation (the empty probe FAILS it);
    # applications is untouched by this timeline and already passes.
    want_c = load_fixture(ctx, "candidates_post_cp1.json")
    cand_diffs = row_diff(candidates, want_c)
    ctx.check(
        "poll_reconcile_candidates_rows_exact",
        not cand_diffs,
        diff_detail("candidates@post_cp1", candidates, want_c, cand_diffs),
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    want_a = load_fixture(ctx, "applications_post_cp1.json")
    app_diffs = row_diff(applications, want_a)
    ctx.check(
        "poll_reconcile_applications_rows_exact",
        not app_diffs,
        diff_detail("applications@post_cp1", applications, want_a, app_diffs),
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # CRUX (poll-only path): docs claim "soft deletes surface as is_deleted:
    # true ... in list responses" (LIES.md lie 3) -- with NO webhook listener
    # running, a competent implementation that built its poll sync around
    # that documented (nonexistent) flag never discovers this deletion at
    # all; cand_0007 stays a permanent phantom. This is the task's core
    # mechanic (primary_mechanic: phantom_delete_flag_detection).
    ctx.check(
        "poll_only_reconciled_delete_via_410",
        c7.get("is_deleted") is True,
        f"cand_0007 is_deleted={c7.get('is_deleted')} (poll-only run, no webhook listener started)",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # No other candidate should have been incorrectly tombstoned by a sloppy
    # reconcile sweep that doesn't actually check 410 before tombstoning.
    #
    # GUARD (evidence-slice rule, WORKLOG 2026-07-29/08-01): `len(wrongly_deleted)
    # == 0` alone is vacuous for a connector that never ran the reconcile sweep at
    # all (or ran it but tombstoned nothing) -- "nothing was WRONGLY tombstoned"
    # is trivially true when NOTHING was tombstoned. Conjoin with the positive
    # evidence one line up (the reconcile sweep actually engaged and correctly
    # confirmed the ONE known deletion) so this only counts once the mechanism
    # this scenario exists to test has demonstrably run.
    reconcile_engaged = c7.get("is_deleted") is True
    wrongly_deleted = [r["source_id"] for r in candidates if r["source_id"] != "cand_0007" and r["is_deleted"]]
    ctx.check(
        "no_other_candidate_incorrectly_tombstoned",
        reconcile_engaged and len(wrongly_deleted) == 0,
        f"reconcile_engaged={reconcile_engaged} unexpected tombstones={wrongly_deleted[:5]}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    await builtin_l2(ctx)

"""Scenario 1 -- the candidate.deleted webhook event is consumed and applied.

Flow:
  1. Backfill (candidates + applications) at checkpoint 0.
  2. Recreate the vendor at checkpoint 1 (the mutation timeline's first entry:
     `cand_0007` deleted, webhook_only -- emits `candidate.deleted` as
     evt_00001; TAMPER_INJECT is always on in this task's compose, so a
     seeded tampered delivery rides along too). Bring the serve listener up
     (gives it the `connector` alias the dispatcher targets), drain until the
     event is acked AND the tampered delivery has been observed rejected,
     then stop it.
  3. Assert the store now reflects the deletion (tombstoned) -- and that
     `cand_0007` was NOT confused with the nonexistent `is_deleted` flag
     (TalentLoop never sends one; the canonical `is_deleted: true` here is
     the connector's OWN representation).

Replaced checks: `backfill_candidates_match_fixture`,
`backfill_applications_match_fixture`, `freshness_candidates_match_fixture` and
`freshness_applications_match_fixture` (whole-document blob compares) are now
the per-row, per-field `*_rows_exact` diffs below, scored per entity from the
empty-probe column: candidates carries the phantom-delete mechanic (+2,
mandatory), applications and both cp0 backfills are already passed by the
starter (0/-1).

Then run the built-in L2 gates (webhook signature/skew hard gates, credential
hygiene, pagination soft checks).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    VENDOR,
    diff_detail,
    drain_webhooks,
    dump_store,
    load_fixture,
    reset_store,
    row_diff,
    serve_start,
    serve_stop,
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
    # correct plumbing (not this task's exercise) and no faults are armed
    # here, so an unmodified starter already runs this cleanly.
    ctx.check(
        "backfill_exit_ok",
        code == 0 and dumped is not None,
        f"exit={code} stderr={err[:400]} store_readable={dumped is not None}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    if dumped is None:
        ctx.check(
            "backfill_store_readable",
            False,
            "dump produced no output",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return

    # Restores the signal of the deleted `backfill_candidates_match_fixture` /
    # `backfill_applications_match_fixture` blob compares. Both are 0/-1 by
    # MEASUREMENT: the empty probe passes both (backfill is provided, correct
    # plumbing and no faults are armed in this phase), so a correct cp0 backfill
    # earns nothing here and only losing it costs.
    cp0_candidates, cp0_applications = dumped
    diffs = row_diff(cp0_candidates, load_fixture(ctx, "candidates_checkpoint_0.json"))
    ctx.check(
        "backfill_candidates_rows_exact",
        not diffs,
        diff_detail("candidates@cp0", cp0_candidates,
                    load_fixture(ctx, "candidates_checkpoint_0.json"), diffs),
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )
    diffs = row_diff(cp0_applications, load_fixture(ctx, "applications_checkpoint_0.json"))
    ctx.check(
        "backfill_applications_rows_exact",
        not diffs,
        diff_detail("applications@cp0", cp0_applications,
                    load_fixture(ctx, "applications_checkpoint_0.json"), diffs),
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # -- 2. cp1 webhooks: serve first, then recreate (dispatcher hits live listener) ----------
    serve_start(ctx)
    try:
        handle.recreate(checkpoint=1)
        delivered = drain_webhooks(ctx, expect_events={"evt_00001"}, expect_tampered=True)
    finally:
        serve_stop(ctx)

    # Requires the connector's own verify -> dedup -> apply/tombstone logic
    # (webhooks.py is entirely IMPLEMENT ME) -- an unmodified starter crashes
    # uncaught on every delivery and never acks. Supporting evidence for the
    # webhook discovery path; webhook_applied_delete below is the outcome.
    ctx.check(
        "webhook_delete_event_delivered",
        delivered,
        "the candidate.deleted event was not acked 2xx (or tampered delivery not observed rejected)",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # -- 3. store reflects the deletion, applied via the webhook path -------
    dumped = dump_store(ctx)
    if dumped is None:
        ctx.check(
            "freshness_store_readable",
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

    # Restores the signal of the deleted `freshness_candidates_match_fixture` /
    # `freshness_applications_match_fixture` blob compares, split by entity
    # because the empty-probe column splits by entity: the candidates key is where the phantom-delete mechanic lives (the empty
    # probe FAILS it), while applications carries no deletion in this timeline
    # and the empty probe already passes it.
    want_c = load_fixture(ctx, "candidates_post_cp1.json")
    cand_diffs = row_diff(candidates, want_c)
    ctx.check(
        "freshness_candidates_rows_exact",
        not cand_diffs,
        diff_detail("candidates@post_cp1", candidates, want_c, cand_diffs),
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    want_a = load_fixture(ctx, "applications_post_cp1.json")
    app_diffs = row_diff(applications, want_a)
    ctx.check(
        "freshness_applications_rows_exact",
        not app_diffs,
        diff_detail("applications@post_cp1", applications, want_a, app_diffs),
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # CRUX (webhook path): docs claim deletes are "also reflected in poll via
    # is_deleted" (LIES.md lie 3) -- a competent implementation faithful to
    # that doc could skip taking the webhook consumption path seriously, or
    # mis-parse the payload (docs also say `event_type`, wire says `type` --
    # lie 1). A do-nothing/doc-trusting connector never lands this tombstone.
    ctx.check(
        "webhook_applied_delete",
        c7.get("is_deleted") is True,
        f"cand_0007 is_deleted={c7.get('is_deleted')}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    await builtin_l2(ctx)

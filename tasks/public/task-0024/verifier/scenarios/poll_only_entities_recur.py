"""Scenario 2 -- job/note freshness has NO signal but a RECURRING poll pass.

This is the scenario that most directly tests whether the connector treated
job/note as a one-shot backfill (reasoning "webhooks will keep things fresh"
the way they do for candidate/application in scenario 1). There is
categorically no webhook event for job/note, ever -- the serve listener is
never even started in this scenario, so the ONLY way `job_0003`'s status
change and `note_0004`'s body edit can ever be discovered is a SECOND,
independent `poll` invocation re-scanning the full collection.

Flow:
  1. Backfill all 4 entities at checkpoint 0 (fresh store) -- `job_0003` and
     `note_0004` in their ORIGINAL state.
  2. Recreate the vendor at checkpoint 5 (the full "CP2" milestone timeline).
     Do NOT start the serve listener at all -- job/note have no webhook path
     to even test, and this also proves the connector isn't accidentally
     depending on one.
  3. Run `poll` a SECOND time against the SAME running vendor (simulating the
     connector's normal recurring poll cadence -- this is the exercise: a
     connector that only ever re-scans once, at backfill time, passes
     scenario 1 and then goes silently stale on every job/note change
     forever).
  4. Assert the store now reflects `job_0003`'s ``status: closed`` and
     `note_0004`'s updated ``body`` -- discoverable ONLY via this second,
     independent poll pass.

The vanish+410 reconcile primitive (``poll.py``'s ``_reconcile_one``) is
shared, kind-agnostic code across all 4 entities -- the SAME code that
scenario 1 exercises for candidate/application deletes is what would also
catch a job/note delete, so this scenario does not duplicate that check;
task-0023's dedicated poll_reconcile.py established the pattern once.

Then run the built-in L2 gates (credential hygiene, pagination soft checks).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    VENDOR,
    diff_detail,
    dump_store,
    grade_cp0_backfill,
    load_fixture,
    reset_store,
    row_diff,
)

FULL_CHECKPOINT = 5


async def run(ctx) -> None:
    handle = ctx.vendor(VENDOR)

    # -- 1. backfill at cp0 --------------------------------------------------
    reset_store(ctx)
    handle.recreate(checkpoint=0)

    code, _out, err = ctx.app.run(["backfill"])

    dumped = dump_store(ctx)
    # AND-ed with store readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run.
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

    grade_cp0_backfill(ctx, dumped)

    # -- 2. recreate at the full timeline -- NO serve listener is ever started
    handle.recreate(checkpoint=FULL_CHECKPOINT)

    # -- 3. a SECOND, independent poll pass (the exercise: recurring, not
    #       one-shot) ---------------------------------------------------------
    code, _out, err = ctx.app.run(["poll"])

    # -- 4. store reflects job/note freshness, discovered WITHOUT any webhook
    dumped = dump_store(ctx)
    # AND-ed with store readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run.
    ctx.check(
        "second_poll_exit_ok",
        code == 0 and dumped is not None,
        f"exit={code} stderr={err[:400]} store_readable={dumped is not None}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    if dumped is None:
        ctx.check(
            "poll_recur_store_readable",
            False,
            "dump produced no output",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return
    candidates, jobs, applications, notes = dumped

    # Restores the signal of the four deleted `poll_recur_{entity}_match_fixture`
    # blob compares. jobs and notes are +2 AND mandatory: they are the only two
    # entities with no webhook path whatsoever, so a whole-store match after the
    # second poll IS this task's mechanic. candidates/applications are +1 --
    # correct, but the recurring poll re-scans full collections, so it converges
    # them even for a connector that never consumed a single webhook (that path
    # is graded, and made mandatory, in webhook_covered_entities.py).
    for entity, store, want_name in (
        ("candidates", candidates, "candidates_post_cp2.json"),
        ("applications", applications, "applications_post_cp2.json"),
    ):
        want = load_fixture(ctx, want_name)
        diffs = row_diff(store, want)
        ctx.check(
            f"poll_recur_{entity}_rows_exact",
            not diffs,
            diff_detail(f"{entity}@post_cp2", store, want, diffs),
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )
    want_jobs = load_fixture(ctx, "jobs_post_cp2.json")
    job_diffs = row_diff(jobs, want_jobs)
    ctx.check(
        "poll_recur_jobs_rows_exact",
        not job_diffs,
        diff_detail("jobs@post_cp2", jobs, want_jobs, job_diffs),
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    want_notes = load_fixture(ctx, "notes_post_cp2.json")
    note_diffs = row_diff(notes, want_notes)
    ctx.check(
        "poll_recur_notes_rows_exact",
        not note_diffs,
        diff_detail("notes@post_cp2", notes, want_notes, note_diffs),
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # CRUX: job/note have NO webhook signal at all, ever (documented
    # truthfully -- not a lie). The trap is treating job/note polling as a
    # one-shot backfill ("webhooks will keep things fresh" reasoning that
    # holds for candidate/application but never for job/note); a connector
    # that does this passes the initial-state check and then silently goes
    # stale on every subsequent job/note change forever. Only a SECOND,
    # independent poll pass can discover these two changes.
    jobs_by_id = {r["source_id"]: r for r in jobs}
    j3 = jobs_by_id.get("job_0003", {})
    ctx.check(
        "poll_recur_applied_job_status",
        j3.get("data", {}).get("status") == "closed",
        f"job_0003 status={j3.get('data', {}).get('status')} (only a SECOND poll pass discovers this)",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    notes_by_id = {r["source_id"]: r for r in notes}
    n4 = notes_by_id.get("note_0004", {})
    ctx.check(
        "poll_recur_applied_note_body",
        n4.get("data", {}).get("body") == "Updated after debrief.",
        f"note_0004 body={n4.get('data', {}).get('body')!r}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    await builtin_l2(ctx)

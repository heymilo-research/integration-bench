"""forced_restart_resume — the star mechanic, driven deterministically.

CHECKPOINT=0, fresh store, and the vendor faulted to answer 500 exactly once
on page 4 of /v1/subjects, page 4 of /v1/checks and page 3 of /v1/reports. The
connector's transport does not swallow 5xx, so each ``sync`` invocation dies
mid-crawl on the first collection whose fault has not fired yet — the
scheduler-kills-us-mid-run event the ticket is about, produced by vendor
configuration instead of by racing the app with ``docker kill`` (see
_scenario_util's header for why the old watchdog could not work).

Between invocations the verifier ages the dead-in-the-water collection's
persisted cursor past ``VT_CURSOR_TTL_REQS=5`` with its own list requests
(request-indexed, per-collection, any client's traffic counts), so the next
invocation's first list request replays a cursor that is guaranteed dead and
gets the documented ``410 cursor_expired``. Four invocations in all: one death
per collection, then the clean finishing pass.

Scoring (2026-08-07). Per entity, so a recovery that mis-anchors one collection
names it instead of collapsing into one opaque zero:

+2 mandatory : post_recovery_rows_exact:{entity} — after the final pass the store
     matches the checkpoint-0 answer key exactly, for each collection
     independently. Mandatory and strictly the strongest statement here: a
     submission is not a solution to this ticket unless all three collections
     come out exactly right after being killed mid-crawl. Recorded under the same
     name by budget_pressure_recovery too, so the deduped instance carries the
     worse of the single-fault and the double-fault recovery.
+2 : post_recovery_no_missing_rows:{entity} — stated separately from equality
     because it is the ticket's own words ("no rows missing"), and because a
     recovery that re-anchors its pass in the wrong place loses rows silently
     while everything it did keep stays byte-perfect. Not additionally mandatory
     — rows_exact already implies it, and gating twice on one property states the
     same bar twice.
+2 : recovery_kept_pass_filter:{entity} — a 410 may cost the connector its
     cursor; it may not move the pass's anchor. Structural, from the vendor's
     request log. Scored but not mandatory: it is the mechanism behind the trap
     rather than the trap itself, and a connector that reaches an exactly-correct
     final state by another route should lose the points without being barred
     from Solved.
0/-1 : no_duplicate_rows_after_recovery, pre_restart_partial_never_corrupt,
     restart_run_exit_ok — the given starter already satisfies all three (its bug
     only costs it rows), so passing must earn nothing and only a regression may
     cost.

Deliberately NOT scored: "a 410 actually reached the connector". That is fault
engagement — evidence the scenario worked, not a property of the submission —
and every submission that issues any request banks it. It is reported in each
recovery_kept_pass_filter detail string instead.

Conduct (builtin_l2) is scored once per verdict, in
watermark_integrity_incremental — the only scenario with no verifier-injected
traffic. builtin_l2 reads the raw request log with no exclusion mechanism, so
scoring it here would grade this module's own probe requests as if the
connector had made them; measured, that produced a false
``no_hot_loop_on_error`` failure against gold.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _scenario_util import (  # noqa: E402
    ENTITIES,
    cursor_expiry_observed,
    dump_store,
    fault_spec,
    fixtures_for,
    interrupted_recovery_drive,
    missing_source_ids,
    no_duplicate_source_ids,
    pass_filter_preserved,
    recreate_vendor,
    reset_store,
    rows_are_correct_subset,
)

# One clean pass needs a single grant; four invocations plus the verifier's own
# throwaway grant need five token calls in the same window, so the documented
# boot-time limit is raised here. budget_pressure_recovery is where the token
# economy itself is under test.
_TOKEN_RL_LIMIT = 30


def _fail_all(ctx, reason: str) -> None:
    for entity in ENTITIES:
        ctx.check(
            f"post_recovery_rows_exact:{entity}",
            False,
            reason,
            pass_value=2,
            fail_value=0,
            mandatory=True,
        )
        ctx.check(
            f"post_recovery_no_missing_rows:{entity}",
            False,
            reason,
            pass_value=2,
            fail_value=0,
            mandatory=False,
        )
        ctx.check(
            f"recovery_kept_pass_filter:{entity}",
            False,
            reason,
            pass_value=2,
            fail_value=0,
            mandatory=False,
        )
    ctx.check(
        "no_duplicate_rows_after_recovery",
        False,
        reason,
        pass_value=0,
        fail_value=-1,
        mandatory=False,
        bucket="soft",
    )


async def run(ctx) -> None:
    reset_store(ctx)
    recreate_vendor(
        ctx,
        checkpoint=0,
        faults=fault_spec(*ENTITIES),
        token_rl_limit=_TOKEN_RL_LIMIT,
    )

    outcome = interrupted_recovery_drive(ctx, ENTITIES)
    fixtures = fixtures_for(ctx, 0)

    # -- the interruption itself: evidence, not credit ------------------------
    # Whatever landed before the first death must be byte-correct (a partial
    # crawl may be short, never wrong). The count is deliberately unpinned:
    # only the fault page is fixed, and a submission is free to crawl the three
    # collections in any order.
    partial = outcome["first_partial"]
    if partial is not None:
        ok, detail = rows_are_correct_subset(
            [r for e in ENTITIES for r in partial[e]],
            [r for e in ENTITIES for r in fixtures[e]],
        )
        ctx.check(
            "pre_restart_partial_never_corrupt",
            ok,
            detail,
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
    else:
        ctx.check("pre_restart_partial_never_corrupt", False,
            "dump produced no output after the first mid-crawl death",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )

    final_code = outcome["exits"][-1]
    store = dump_store(ctx)
    # AND-ed with store readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run.
    ctx.check("restart_run_exit_ok",
        final_code == 0 and store is not None,
        f"round exits={outcome['exits']} store_readable={store is not None} "
        f"last stderr={outcome['last_stderr'][:300]}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    if store is None:
        _fail_all(ctx, "dump produced no output after the final restart")
        return

    connector_requests = outcome["ager"].connector_requests()
    # Fault engagement is EVIDENCE, not credit: whether a 410 reached the
    # connector is reported inside each recovery_kept_pass_filter detail
    # string (auditable in the verdict) rather than scored as its own check.
    # Scoring it would hand every submission that merely issues requests a free
    # pass, and would penalise a connector that legitimately never let a cursor
    # expire (e.g. one that retries the injected 500 in-process) even though
    # its final state is complete.
    seen_410, detail_410 = cursor_expiry_observed(connector_requests)

    for entity in ENTITIES:
        rows, fixture = store[entity], fixtures[entity]
        ctx.check(f"post_recovery_rows_exact:{entity}",
            rows == fixture,
            f"rows={len(rows)} want={len(fixture)}",
            pass_value=2,
            fail_value=0,
            mandatory=True,
        )
        missing = missing_source_ids(rows, fixture)
        ctx.check(f"post_recovery_no_missing_rows:{entity}",
            not missing,
            f"{len(missing)} of {len(fixture)} row(s) never ingested: {missing[:5]}",
            pass_value=2,
            fail_value=0,
            mandatory=False,
        )
        ok, detail = pass_filter_preserved(connector_requests, entity)
        ctx.check(f"recovery_kept_pass_filter:{entity}",
            ok,
            f"{detail} [{detail_410}]" if not seen_410 else detail,
            pass_value=2,
            fail_value=0,
            mandatory=False,
        )

    dupes = [e for e in ENTITIES if not no_duplicate_source_ids(store[e])]
    ctx.check("no_duplicate_rows_after_recovery",
        not dupes,
        f"duplicate source_id(s) found in: {dupes}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
        bucket="soft",
    )

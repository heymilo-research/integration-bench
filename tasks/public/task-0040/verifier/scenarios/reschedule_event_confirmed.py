"""reschedule_event_confirmed — the 202 is provisional, the event commits.

Flow:
  1. Recreate the vendor at checkpoint 0 (clean writeback store, no prior
     idempotency state).
  2. Bring `serve` up first (gets the `connector` alias the vendor's
     WEBHOOK_TARGET points at), THEN run `push` once. `push` posts both
     staged items:
       - resched-1 (itv_0100, valid `scheduled_at`) -> 202 provisional ack.
       - resched-2 (itv_0105, missing `scheduled_at`) -> 422 field_errors,
         reported as a failure, never retried, never confirmed.
  3. Immediately after `push` returns, resched-1 must be PENDING, not
     COMMITTED — a connector that marks it done on the 202 alone is wrong.
  4. Poll (via `dump`) until resched-1 transitions to COMMITTED once the
     vendor's async `interview.rescheduled` confirmation event lands and is
     verified/applied. resched-2 stays FAILED throughout (never retried into
     existence).
  5. Final `writeback_result.json` matches the fixture exactly.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    load_fixture,
    read_output,
    serve_start,
    serve_stop,
    wait_for_status,
)


async def run(ctx) -> None:
    handle = ctx.vendor("interviewly")
    handle.recreate(checkpoint=0)

    serve_start(ctx)
    try:
        marker_ts = max((e.get("ts", 0) for e in handle.request_log()), default=-1.0)
        code, _out, err = ctx.app.run(["push"])
        # AND-ed with this push's OWN data-plane traffic (task-0043 pattern,
        # 2026-08-02): exit 0 alone is vacuously bankable by a do-nothing run,
        # and idempotent_retry.py writes the same writeback_result.json so its
        # readability is not this run's evidence. Gold's push always POSTs the
        # reschedule (that 202 is this scenario's whole subject).
        push_calls = [
            e for e in handle.request_log()
            if e.get("ts", 0) > marker_ts and e.get("path") not in ("/", "")
        ]
        ctx.check(
            "push_exit_ok",
            code == 0 and len(push_calls) > 0,
            f"exit={code} data_plane_calls={len(push_calls)} stderr={err[:400]}",
            # client.reschedule/writeback.py are fully unimplemented in the
            # starter (raise NotImplementedError) -- a do-nothing submission
            # crashes here, so this is required, just not itself the crux.
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )

        # Immediately after the push, the 202 must NOT have been treated as a
        # commit -- resched-1 should still read PENDING right now.
        immediate = read_output(ctx, "writeback_result.json") or {}
        immediate_row = next(
            (r for r in immediate.get("reschedules", []) if r.get("client_ref") == "resched-1"),
            {},
        )
        ctx.check(
            "202_treated_as_provisional_not_committed",
            immediate_row.get("status") == "pending",
            f"resched-1 status right after push={immediate_row.get('status')!r} (expected 'pending')",
            # The trap: a competent implementation that treats the 202
            # Accepted as a commit signal fails this. It is what the task
            # ("commit_on_event_not_202") is about.
            pass_value=2,
            fail_value=0,
            mandatory=True,
        )

        confirmed = wait_for_status(ctx, "resched-1", "committed", timeout_s=90.0)
        ctx.check(
            "committed_on_confirming_event",
            confirmed is not None,
            "resched-1 never transitioned to 'committed' after the confirming webhook event",
            # The complementary half of the same trap: not committing too
            # early is not enough -- the connector must also actually reach
            # 'committed' once the confirming event lands.
            pass_value=2,
            fail_value=0,
            mandatory=True,
        )
        if confirmed is not None:
            ctx.check(
                "confirmation_event_id_recorded",
                bool(confirmed.get("confirmation_event_id")),
                f"resched-1 confirmation_event_id={confirmed.get('confirmation_event_id')!r}",
                # Supporting detail -- could also be satisfied by the
                # reconcile_pending backstop (event_id="reconciled-by-poll"),
                # not exclusively proof of the event-confirm path.
                pass_value=1,
                fail_value=0,
                mandatory=False,
            )
    finally:
        serve_stop(ctx)

    # Split per client_ref rather than one whole-document compare. The old
    # aggregate was justified in-tree (it is the ONLY coverage of resched-2's
    # 422/failed path, which is never retried into existence) — splitting keeps
    # that coverage while making it explicit which ref disagreed and on which
    # field, and it drops the last `== fixture` in this task.
    final = read_output(ctx, "writeback_result.json") or {}
    fixture = load_fixture(ctx, "writeback_result.json")
    got_by_ref = {r.get("client_ref"): r for r in final.get("reschedules", [])}
    want_by_ref = {r.get("client_ref"): r for r in fixture.get("reschedules", [])}
    for ref in sorted(set(want_by_ref) | set(got_by_ref), key=str):
        want_row, got_row = want_by_ref.get(ref), got_by_ref.get(ref)
        if got_row is None:
            detail = f"{ref}: missing from writeback_result.json (expected {want_row})"
        elif want_row is None:
            detail = f"{ref}: unexpected entry {got_row}"
        else:
            bad = sorted(k for k in set(want_row) | set(got_row)
                         if want_row.get(k) != got_row.get(k))
            detail = (f"{ref}: fields {bad} differ; got="
                      f"{ {k: got_row.get(k) for k in bad} } want="
                      f"{ {k: want_row.get(k) for k in bad} }") if bad else f"{ref}: matches"
        ctx.check(
            f"writeback_row_fields_exact::{ref}",
            got_row is not None and want_row is not None and got_row == want_row,
            detail,
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )

    await builtin_l2(ctx)

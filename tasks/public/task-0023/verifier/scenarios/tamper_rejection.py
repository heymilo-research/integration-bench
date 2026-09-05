"""Scenario 3 (L3) -- tamper rejection with exactly-once delete application
under LIVE duplicate/out-of-order delivery.

Rejecting a mis-signed delivery under a live listener -- while still applying
the genuine delete event exactly once despite TalentLoop's always-on ~10%
duplicate rate and out-of-order shuffle -- is this connector's core conduct
competency, so this is a declared L3 fault-injection scenario. Seeded tamper
injection is always on for TalentLoop (vendor.yaml
webhooks.delivery.tamper_inject: seeded; this task's compose sets
TAMPER_INJECT=1 defensively since the shipped image gates it on that env var).

Flow:
  1. Backfill at checkpoint 0, then recreate the vendor at checkpoint 1. Its
     built-in dispatcher pushes the candidate.deleted event (with live seeded
     duplicates/reordering) PLUS exactly one extra delivery carrying a
     deliberately invalid signature (and a stale timestamp).
  2. Bring the serve listener up; drain until the genuine event is acked
     (2xx) and the tampered delivery has been REJECTED (non-2xx).
  3. Assert (L3):
       - a tampered delivery was actually sent and NEVER accepted 2xx;
       - the final store matches the post-cp1 answer key (proves the tamper
         had no effect and dedup/reorder handling held).

Then run the built-in L2 hard gates (webhook signature/skew hard gates,
credential hygiene, pagination soft checks).

Replaced checks: `tamper_candidates_match_fixture` and
`tamper_applications_match_fixture` are now the per-row, per-field
`tamper_*_rows_exact` diffs below -- candidates carries the tombstone a
tampered delivery must be unable to forge or suppress (+2, mandatory);
applications is untouched by this timeline and the starter already passes it
(0/-1).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    VENDOR,
    _is_2xx,
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

    # -- 1. backfill at cp0, then cp1 (valid + seeded tampered deliveries) --
    reset_store(ctx)
    handle.recreate(checkpoint=0)
    marker_ts = max((e.get("ts", 0) for e in handle.request_log()), default=-1.0)
    code, _out, err = ctx.app.run(["backfill"])
    # AND-ed with this phase's own data-plane traffic (task-0043 pattern,
    # 2026-08-02): exit 0 alone is vacuously bankable by a do-nothing run, and
    # the store is not dumped in this scenario. Bare "/" healthcheck pings
    # don't count.
    backfill_calls = [
        e for e in handle.request_log()
        if e.get("ts", 0) > marker_ts and e.get("path") not in ("/", "")
    ]
    # Backfill is provided, correct plumbing and no faults are armed here, so
    # an unmodified starter already runs this cleanly.
    ctx.check(
        "tamper_backfill_exit_ok",
        code == 0 and len(backfill_calls) > 0,
        f"exit={code} data_plane_calls={len(backfill_calls)} stderr={err[:400]}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    serve_start(ctx)
    try:
        handle.recreate(checkpoint=1)
        drained = drain_webhooks(ctx, expect_events={"evt_00001"}, expect_tampered=True)
    finally:
        serve_stop(ctx)

    # Requires the connector's own verify -> dedup -> apply/tombstone logic;
    # an unmodified starter crashes uncaught on every delivery and never acks.
    ctx.check(
        "tamper_drained",
        drained,
        "genuine event not acked or tampered delivery not observed rejected",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    deliveries = handle.webhook_deliveries()

    # L3: a tampered delivery was actually sent (so the gate is really
    # tested). This is a VENDOR-side fact (the dispatcher fires it
    # unconditionally) -- not connector behaviour, so it cannot earn credit;
    # it is a precondition-validity sanity check only.
    tampered = [d for d in deliveries if d.get("tampered")]
    ctx.check(
        "tampered_delivery_present",
        len(tampered) >= 1,
        f"tampered deliveries={len(tampered)}",
        pass_value=0,
        fail_value=0,
        mandatory=False,
    )

    # L3: the tampered delivery was NEVER accepted (2xx) by the listener.
    # Rejected BY A LIVE LISTENER: a real non-2xx HTTP response, not a
    # connection failure. The dispatcher logs `status_code: None` when nothing
    # answers, so "never accepted" alone passes vacuously for a connector that
    # never listens; requiring an actual non-2xx response (and the genuine
    # event's 2xx ack, via `drained`) makes this an achievement, not a
    # prohibition (2026-07-29 probe-inversion WORKLOG entry). Required
    # security conduct, not already satisfied by an unmodified starter
    # (which never sends a real HTTP response at all -- see tamper_drained).
    tampered_accepted = [d for d in tampered if _is_2xx(d.get("status_code"))]
    tampered_responded = [
        d for d in tampered
        if d.get("status_code") is not None and not _is_2xx(d.get("status_code"))
    ]
    ctx.check(
        "tampered_delivery_rejected",
        len(tampered) >= 1
        and len(tampered_accepted) == 0
        and len(tampered_responded) >= 1
        and drained,
        f"tampered={len(tampered)} accepted={len(tampered_accepted)} "
        f"responded_non2xx={len(tampered_responded)} genuine_acked={drained}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # L3: the seeded duplicate rate/out-of-order shuffle didn't break dedup --
    # store correct, tamper had no effect.
    dumped = dump_store(ctx)
    if dumped is None:
        ctx.check(
            "tamper_store_readable",
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

    # Restores the signal of the deleted `tamper_candidates_match_fixture` /
    # `tamper_applications_match_fixture` blob compares, split by entity
    # because the empty-probe column splits by entity: candidates carries the tombstone the tampered delivery must not have
    # been able to forge or suppress (the empty probe FAILS it); applications
    # is untouched and already passes.
    want_c = load_fixture(ctx, "candidates_post_cp1.json")
    cand_diffs = row_diff(candidates, want_c)
    ctx.check(
        "tamper_candidates_rows_exact",
        not cand_diffs,
        diff_detail("candidates@post_cp1", candidates, want_c, cand_diffs),
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    want_a = load_fixture(ctx, "applications_post_cp1.json")
    app_diffs = row_diff(applications, want_a)
    ctx.check(
        "tamper_applications_rows_exact",
        not app_diffs,
        diff_detail("applications@post_cp1", applications, want_a, app_diffs),
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    ctx.check(
        "exactly_once_delete_applied",
        c7.get("is_deleted") is True,
        f"cand_0007 is_deleted={c7.get('is_deleted')}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    await builtin_l2(ctx)

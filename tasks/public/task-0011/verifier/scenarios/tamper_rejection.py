"""Scenario 3 (L3) -- tamper rejection with exactly-once application under
LIVE duplicate/out-of-order delivery.

Rejecting a mis-signed delivery under a live listener -- while still applying
every genuine event exactly once despite TalentForge's always-on ~20%
duplicate rate and out-of-order shuffle -- is this connector's core conduct
competency, so this is a declared L3 fault-injection scenario. Seeded tamper
injection is always on for TalentForge (vendor.yaml
webhooks.delivery.tamper_inject: seeded; this task's compose sets
TAMPER_INJECT=1 defensively since the shipped image gates it on that env var).

Flow:
  1. Backfill at checkpoint 0. Bring the serve listener up, then step the
     vendor through checkpoints 1..4 ONE AT A TIME (never jump straight to 4
     -- see _scenario_util.drain_checkpoint_events for why: the dispatcher's
     queue and the delivery log are both scoped to a single boot). Each of
     the four boots pushes that checkpoint's one genuine mutation event PLUS
     its own seeded tampered delivery (deliberately invalid signature, stale
     timestamp) -- draining after each step before the next recreate wipes
     the log.
  2. Drain each step until its genuine event is acked (2xx) and that step's
     tampered delivery has been REJECTED (non-2xx), concatenating every
     step's delivery-log entries for the L3 checks below.
  3. Assert (L3):
       - a tampered delivery was actually sent (across the four boots) and
         NEVER accepted 2xx;
       - the final store matches the post-cp4 answer key (same as freshness:
         proves the tamper had no effect and dedup/reorder handling held).

Then run the built-in L2 hard gates (credential hygiene; the generic
webhook-signature/skew hard gates are vacuous for this vendor's delivery-log
field names -- see _scenario_util's module docstring -- so the L3 checks above
are this scenario's real tamper-rejection assertion, mirroring task-0002's and
task-0005's precedent).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    VENDOR,
    _is_2xx,
    diff_detail,
    drain_checkpoint_events,
    dump_store,
    load_fixture,
    reset_store,
    row_count_ok,
    serve_start,
    serve_stop,
    store_row_diff,
)


async def run(ctx) -> None:
    handle = ctx.vendor(VENDOR)

    # -- 1. backfill at cp0 ---------------------------------------------------
    reset_store(ctx)
    handle.recreate(checkpoint=0)
    marker_ts = max((e.get("ts", 0) for e in handle.request_log()), default=-1.0)
    code, _out, err = ctx.app.run(["backfill"])
    # AND-ed with the backfill's own data-plane traffic (task-0043 pattern,
    # 2026-08-02): exit 0 alone is vacuously bankable by a do-nothing run
    # (the store is only dumped at the very end here, so traffic is the
    # phase-scoped evidence).
    backfill_calls = [
        e for e in handle.request_log()
        if e.get("ts", 0) > marker_ts and e.get("path") not in ("/", "")
    ]
    ctx.check("tamper_backfill_exit_ok",
        code == 0 and len(backfill_calls) > 0,
        f"exit={code} data_plane_calls={len(backfill_calls)} stderr={err[:400]}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # -- 2. cp1..cp4, one boot at a time (valid + seeded tampered per boot) --
    serve_start(ctx)
    try:
        steps = [
            (1, {"evt_00001"}),
            (2, {"evt_00002"}),
            (3, {"evt_00003"}),
            (4, {"evt_00004"}),
        ]
        drained, deliveries = drain_checkpoint_events(ctx, steps)
    finally:
        serve_stop(ctx)

    ctx.check("tamper_drained",
        drained,
        "not all genuine events acked or a tampered delivery not observed rejected",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # L3: a tampered delivery was actually sent (so the gate is really tested).
    tampered = [d for d in deliveries if d.get("tampered")]
    ctx.check("tampered_delivery_present",
        len(tampered) >= 1 and drained,
        f"tampered deliveries={len(tampered)} genuine_acked={drained}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # L3: the tampered delivery was NEVER accepted (2xx) by the listener.
    # Rejected BY A LIVE LISTENER: a real non-2xx HTTP response, not a
    # connection failure. The dispatcher logs `status_code: None` when nothing
    # answers, so "never accepted" alone passes vacuously for a connector that
    # never listens; requiring an actual non-2xx response (and the genuine
    # event's 2xx ack, via `drained`) makes this an achievement, not a
    # prohibition (2026-07-29 probe-inversion WORKLOG entry).
    tampered_accepted = [d for d in tampered if _is_2xx(d.get("status_code"))]
    tampered_responded = [
        d for d in tampered
        if d.get("status_code") is not None and not _is_2xx(d.get("status_code"))
    ]
    ctx.check("tampered_delivery_rejected",
        len(tampered) >= 1
        and len(tampered_accepted) == 0
        and len(tampered_responded) >= 1
        and drained,
        f"tampered={len(tampered)} accepted={len(tampered_accepted)} "
        f"responded_non2xx={len(tampered_responded)} genuine_acked={drained}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
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

    # Replaces the two deleted `tamper_*_match_fixture` whole-store compares.
    # Those said only "rows=N fixture=M", which is exactly the wrong summary for
    # this scenario: the tampered delivery carries a DIFFERENT phone for an
    # EXISTING candidate, so a connector that applied it has the right row count
    # and the wrong data — the one failure the row count cannot see. Split into a
    # count (did dedup/reorder lose or duplicate rows) and a per-field diff (did
    # anything land wrong), each naming the record.
    want_candidates = load_fixture(ctx, "candidates_post_cp4.json")
    want_applications = load_fixture(ctx, "applications_post_cp4.json")

    # Candidates: +1. cp1..cp4 add cand_0900 and tombstone cand_0017, so the count
    # only comes out right if the create landed and the delete did not drop a row.
    ok, detail = row_count_ok(candidates, want_candidates)
    ctx.check(
        "tamper_row_count:candidates",
        ok,
        detail,
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    # Applications: 0/-1. MEASURED on the empty probe — the starter already passes
    # it, because cp4's only application mutation is app_0005's STAGE change and
    # the row count is unmoved by it. Scoring this +1 paid the do-nothing starter
    # for a number it could not get wrong. The per-field check below is where the
    # stage change is actually graded.
    ok, detail = row_count_ok(applications, want_applications)
    ctx.check(
        "tamper_row_count:applications",
        ok,
        detail,
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )
    for label, got, want in (
        ("candidates", candidates, want_candidates),
        ("applications", applications, want_applications),
    ):
        diffs = store_row_diff(got, want)
        ctx.check(
            f"tamper_fields_exact:{label}",
            not diffs,
            diff_detail(diffs),
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )

    by_id = {r["source_id"]: r for r in candidates}
    c42 = by_id.get("cand_0042", {})
    ctx.check("exactly_once_applied",
        c42.get("data", {}).get("phone") == "+1-555-0142",
        f"cand_0042 phone={c42.get('data', {}).get('phone')}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    await builtin_l2(ctx)

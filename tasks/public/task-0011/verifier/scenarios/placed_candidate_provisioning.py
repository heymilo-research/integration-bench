"""Scenario 4 (L3) -- the event bridge: a placed candidate provisions exactly
one Onboardly packet, across two vendors, under the always-on delivery storm.

Three-system dance: TalentForge's webhook payloads carry ONLY the entity id,
so the listener must (1) fetch the candidate's current state from TalentForge
-- the id-only event says nothing about WHY it fired; (2) decide against the
business rule (pipeline_status == "placed"); (3) land exactly one packet in
Onboardly -- a second, wholly independent vendor with static-key auth and its
own failure vocabulary -- and confirm what was actually created. TalentForge's
~20%-duplicate + out-of-order delivery is always on, so the same logical
change reaches the listener repeatedly; Onboardly must still end up with ONE
packet for cand_0099 and NOTHING for anyone else.

Timeline (mutations.py): cp1 = cand_0042 update (phone -- NOT placed; the
bridge must see it and decline), cp5 = cand_0099 update (pipeline_status ->
"placed"; the one provisioning trigger). The replay leg recreates TalentForge
at cp5 a second time -- the dispatcher rebuilds and redelivers the full cp5
plan -- and the packet count must not move.

L1  : backfill exits; bridge_result.json matches the answer key.
L3  : exactly-once across vendors (one distinct accepted create, one distinct
      Idempotency-Key, candidate_id == cand_0099 -- all conjoined with the
      fixture match so a bridge that never posts cannot bank it);
      nothing created for a non-placed candidate (conjoined with the cand_0099
      create existing); the create was confirmed via GET-by-id on Onboardly
      AFTER the create (same-container log, so ts ordering is valid here).
L2  : builtin conduct gates against the PRIMARY vendor -- skipped (early
      return) when the run produced no report, so a do-nothing submission
      cannot bank vacuous prohibitions (2026-07-29 probe-inversion WORKLOG).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2
from bench.verifier.io import read_json_output

from _scenario_util import (  # noqa: E402
    VENDOR,
    bridge_report_diff,
    diff_detail,
    drain_checkpoint_events,
    reset_store,
    serve_start,
    serve_stop,
)

PLACED_CANDIDATE = "cand_0099"
NONPLACED_CANDIDATE = "cand_0042"


def _packet_creates(log):
    return [
        e for e in log
        if str(e.get("method", "")).upper() == "POST"
        and str(e.get("path", "")).rstrip("/").endswith("/v1/packets")
    ]


def _status(e):
    try:
        return int(e.get("status"))
    except (TypeError, ValueError):
        return None


async def run(ctx) -> None:
    tf = ctx.vendor(VENDOR)
    ob = ctx.vendor("onboardly")

    # Fresh onboardly world: packet counter, idempotency cache and logs all
    # reset, so the created packet's id is deterministic run to run.
    ob.recreate(checkpoint=0)

    reset_store(ctx)
    try:
        (ctx.output_dir / "bridge_result.json").unlink()
    except OSError:
        pass

    tf.recreate(checkpoint=0)
    marker_ts = max((e.get("ts", 0) for e in tf.request_log()), default=-1.0)
    code, _out, err = ctx.app.run(["backfill"])
    # AND-ed with the backfill's own data-plane traffic (task-0043 pattern,
    # 2026-08-02): exit 0 alone is vacuously bankable by a do-nothing run.
    # bridge_result.json is produced by the LATER bridge phase, not by this
    # one, so traffic is this phase's own evidence; the compose healthcheck's
    # bare "/" pings don't count.
    backfill_calls = [
        e for e in tf.request_log()
        if e.get("ts", 0) > marker_ts and e.get("path") not in ("/", "")
    ]
    ctx.check("bridge_backfill_exit_ok",
        code == 0 and len(backfill_calls) > 0,
        f"exit={code} data_plane_calls={len(backfill_calls)} stderr={err[:400]}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # -- 1. the decision pair: cp1 (updated, NOT placed) then cp5 (placed) ---
    serve_start(ctx)
    try:
        delivered, _ = drain_checkpoint_events(
            ctx, [(1, {"evt_00001"}), (5, {"evt_00005"})]
        )
    finally:
        serve_stop(ctx)
    ctx.check("bridge_events_delivered",
        delivered,
        "cp1/cp5 events were not acked 2xx by the listener",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # -- 2. replay: the full cp5 plan is redelivered (fresh dispatcher boot) --
    # Cost guard: when leg 1 never delivered (a listener that never came up or
    # never acked), the replay proves nothing extra and each drain would burn
    # its full 150s timeout on every do-nothing probe grading. Record the same
    # check outcomes and skip the leg — gold always delivers, so the gold path
    # (and the verdict topology) is identical either way.
    if delivered:
        serve_start(ctx)
        try:
            replay_delivered, _ = drain_checkpoint_events(ctx, [(5, {"evt_00005"})])
        finally:
            serve_stop(ctx)
    else:
        replay_delivered = False
    ctx.check("bridge_replay_drained",
        replay_delivered,
        "replayed cp5 events were not acked 2xx (a dedup-drop must still ack)"
        if delivered else "leg 1 never delivered; replay skipped and recorded failed",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # -- 3. the report ---------------------------------------------------------
    result = read_json_output(ctx.output_dir / "bridge_result.json", timeout_s=15.0)
    if result is None:
        ctx.check(
            "bridge_result_readable",
            False,
            "missing/unreadable bridge_result.json",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        # Unrolled from a `for name in (...)` loop (2026-08-07): the three checks
        # no longer share one scoring, and a loop over names cannot state three
        # different ones. It also made every value here invisible to
        # tools/check_migration.py, which can only read literals — and the
        # early-return path MUST carry the same values as the main path below, or
        # the deduped instance's scoring is arbitrary.
        ctx.check(
            "packets_exactly_once_cross_vendor",
            False,
            "no bridge_result.json",
            pass_value=2,
            fail_value=0,
            mandatory=True,
        )
        ctx.check(
            "no_packet_for_nonplaced_candidate",
            False,
            "no bridge_result.json",
            pass_value=2,
            fail_value=0,
            mandatory=True,
        )
        ctx.check(
            "confirmed_via_get_by_id",
            False,
            "no bridge_result.json",
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )
        # No output -> nothing to judge; skip builtin_l2 so the stub cannot
        # bank vacuous prohibitions (load-bearing early return).
        return
    ctx.check(
        "bridge_result_readable",
        True,
        "bridge_result.json parsed",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    import json as _json
    fixture = _json.loads((ctx.fixtures / "bridge_result.json").read_text(encoding="utf-8"))
    # Replaces the deleted `bridge_result_matches_fixture` whole-document compare,
    # whose detail string could say only "provisioned=1 skipped=1" — the two counts
    # a wrong report is most likely to get right. Differences now name the section,
    # the candidate and the field: "provisioned[cand_0099].packet.status: got=
    # 'active' want='draft'", or "skipped[cand_0042]: missing".
    report_diffs = bridge_report_diff(result, fixture)
    report_ok = not report_diffs
    ctx.check(
        "bridge_report_matches_answer_key",
        report_ok,
        diff_detail(report_diffs),
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    # The two cross-vendor checks below were conjoined with the old blob compare's
    # boolean, to stop a bridge that never posts from banking them. `report_ok`
    # carries that role now — same guard, per-field evidence.
    fixture_ok = report_ok

    # -- 4. forensics on the Onboardly request log ----------------------------
    ob_log = ob.request_log()
    creates = _packet_creates(ob_log)
    accepted = [e for e in creates if _status(e) == 201]
    created_cids = {
        (e.get("body") or {}).get("candidate_id")
        for e in accepted
        if isinstance(e.get("body"), dict)
    }
    idem_keys = {
        v
        for e in accepted
        for k, v in (e.get("headers") or {}).items()
        if k.lower() == "idempotency-key"
    }
    ctx.check("packets_exactly_once_cross_vendor",
        created_cids == {PLACED_CANDIDATE} and len(idem_keys) == 1 and fixture_ok,
        f"accepted_creates={len(accepted)} candidate_ids={sorted(x for x in created_cids if x)} "
        f"distinct_idempotency_keys={len(idem_keys)} fixture_ok={fixture_ok}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    leaked = sorted(x for x in created_cids if x and x != PLACED_CANDIDATE)
    ctx.check("no_packet_for_nonplaced_candidate",
        not leaked and PLACED_CANDIDATE in created_cids,
        f"non-placed candidate_ids in creates={leaked} "
        f"(placed create present={PLACED_CANDIDATE in created_cids})",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # Confirmation: a GET /v1/packets/{id} 200 AFTER the first accepted create.
    # Both entries come from the SAME onboardly container/process, so the
    # monotonic ts values are comparable (unlike cross-vendor ordering).
    first_create_ts = min((e.get("ts", 0) for e in accepted), default=None)
    confirm_gets = [
        e for e in ob_log
        if str(e.get("method", "")).upper() == "GET"
        and "/v1/packets/" in str(e.get("path", ""))
        and _status(e) == 200
        and first_create_ts is not None
        and e.get("ts", 0) > first_create_ts
    ]
    ctx.check("confirmed_via_get_by_id",
        bool(confirm_gets) and fixture_ok,
        f"confirming GETs after create={len(confirm_gets)} fixture_ok={fixture_ok}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    await builtin_l2(ctx)

"""writeback_lands_on_new_vendor -- post-cutover writes must target Bullpen
v2 exclusively, never StaffLine (the pre-migration behavior this ticket
must retire).

Continues from wherever cutover_backfill_to_bullpen.py left the stack.
Runs `python -m staffline_to_bullpen_migrate writeback` TWICE in a row (the
second run simulates retrying after a timeout/connection error): the
pending batch (writeback_requests.py) must land on Bullpen only, and the
retry must reuse the exact same Idempotency-Key per logical write rather
than minting a fresh one (a fresh key on retry would double the side
effect on a vendor that dedupes by key).

L1 : both invocations exit 0; the first run's writeback_result.json matches
     the answer-key fixture (every write ok=true).
L3 : no_writes_to_legacy_post_cutover -- zero requests of ANY kind hit
     vendor-legacy across both invocations (StaffLine is read-only from
     the baseline onward); writes_land_on_bullpen -- at least one POST/
     PATCH against vendor-new succeeded (200) for each pending op;
     idempotency_key_reused_on_retry -- the second invocation's
     Idempotency-Key for each op-target pair is IDENTICAL to the first
     invocation's (proof of deterministic, retry-safe keys, not fresh
     UUIDs per run).
L2  : builtin conduct gates/soft checks.
"""

from __future__ import annotations

from bench.verifier.builtin_l2 import builtin_l2
import json

from bench.verifier.io import read_json_output


def _write_calls(log):
    return [e for e in log if e.get("method") in ("POST", "PATCH") and str(e.get("path", "")).startswith("/v2/")]


def _key_by_path(calls):
    return {e.get("path"): e.get("idempotency_key") for e in calls}


def _row_diff(rows, want):
    """Per-source_id, per-field comparison against an answer key.

    Restores the signal of the deleted blob compares
    `candidates_matches_baseline_fixture`, `jobs_matches_baseline_fixture`,
    `applications_matches_baseline_fixture`,
    `candidates_matches_final_fixture`, `jobs_matches_final_fixture`,
    `applications_matches_final_fixture` and `writeback_result_matches_fixture`.
    """
    if rows is None:
        return [{"source_id": "<no output>", "field": "<missing or unreadable>"}]
    got_by_id = {r.get("source_id"): r for r in rows}
    want_by_id = {r.get("source_id"): r for r in want}
    diffs = []
    for sid in sorted(set(want_by_id) | set(got_by_id), key=str):
        w, g = want_by_id.get(sid), got_by_id.get(sid)
        if g is None:
            diffs.append({"source_id": sid, "field": "<missing row>"})
            continue
        if w is None:
            diffs.append({"source_id": sid, "field": "<unexpected row>"})
            continue
        for key in sorted(set(w) | set(g)):
            if w.get(key) != g.get(key):
                diffs.append({"source_id": sid, "field": key,
                              "want": w.get(key), "got": g.get(key)})
    return diffs


def _diff_detail(label, rows, want, diffs, limit=3):
    n = "none" if rows is None else len(rows)
    if not diffs:
        return f"{label}: {n} row(s), every field matches the answer key"
    shown = json.dumps(diffs[:limit], sort_keys=True, default=str)
    more = f" (+{len(diffs) - limit} more)" if len(diffs) > limit else ""
    return f"{label}: rows={n} expected={len(want)}; {len(diffs)} field diff(s): {shown}{more}"


async def run(ctx) -> None:
    legacy = ctx.vendor("vendor-legacy")
    new = ctx.vendor("vendor-new")

    marker_legacy_ts = max((e.get("ts", 0) for e in legacy.request_log()), default=-1.0)
    marker_new_ts = max((e.get("ts", 0) for e in new.request_log()), default=-1.0)

    exit_code, _stdout, stderr = ctx.app.run(["writeback"])

    out_path = ctx.output_dir / "writeback_result.json"
    out = read_json_output(out_path, timeout_s=15.0 if exit_code == 0 else 0.5)
    # AND-ed with output readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run.
    ctx.check(
        "app_exit_ok",
        exit_code == 0 and out is not None,
        f"exit={exit_code} stderr={stderr[:500]} output_readable={out is not None}",
        # A do-nothing/unmodified starter already exits 0 with a readable
        # (if wrong-vendor) writeback_result.json -- see writes_land_on_bullpen
        # / no_writes_to_legacy_post_cutover below for the actual trap.
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )
    if out is None:
        ctx.check(
            "writeback_result_exists",
            False,
            f"missing/unreadable {out_path.name}",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )

    first_new_calls = [e for e in new.request_log() if e.get("ts", 0) > marker_new_ts]
    first_keys = _key_by_path(_write_calls(first_new_calls))

    # ---------------------------------------------------------- retry pass
    marker_new_ts_2 = max((e.get("ts", 0) for e in new.request_log()), default=-1.0)
    exit_code2, _stdout2, stderr2 = ctx.app.run(["writeback"])

    second_new_calls = [e for e in new.request_log() if e.get("ts", 0) > marker_new_ts_2]
    second_keys = _key_by_path(_write_calls(second_new_calls))
    # AND-ed with the retry pass's own Bullpen data-plane traffic (task-0043
    # pattern, 2026-08-02): exit 0 alone is vacuously bankable by a
    # do-nothing run. The retry writes the same writeback_result.json path
    # as the first pass, so traffic — not the artifact — is the retry's own
    # evidence; gold's retry always re-sends the keyed writes
    # (idempotency_key_reused_on_retry below depends on exactly that).
    second_data_plane = [
        e for e in second_new_calls
        if str(e.get("path", "")).startswith(("/v1/", "/v2/", "/oauth"))
    ]
    ctx.check(
        "retry_app_exit_ok",
        exit_code2 == 0 and len(second_data_plane) > 0,
        f"exit={exit_code2} stderr={stderr2[:500]} data_plane_calls={len(second_data_plane)}",
        # Pure "did it run + talked to something" plumbing (the /oauth
        # token-mint path alone satisfies "data plane calls>0", so a
        # connector that merely constructs a Bullpen client without ever
        # issuing a real write banks this) -- never earns credit on its
        # own; writes_land_on_bullpen / idempotency_key_reused_on_retry
        # below are the precise trap.
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # ------------------------------------------------------------------- L3
    # Exclude bare "/" pings: docker-compose's healthcheck polls
    # http://localhost:8000/ every 2s for the container's entire lifetime,
    # independent of anything the connector does, and lands in the same
    # requests.jsonl this check inspects. Only /svc/* is a real StaffLine
    # data-plane call this connector could have made.
    legacy_calls = [
        e for e in legacy.request_log()
        if e.get("ts", 0) > marker_legacy_ts and str(e.get("path", "")).startswith("/svc/")
    ]
    # Evidence gate (mirrors builtin_l2's traffic-conditional rule): "writes
    # never hit StaffLine" is only meaningful once the writeback phase
    # actually talked to a vendor — gate on this phase's own Bullpen
    # data-plane traffic (both invocations). Gold's writeback always lands
    # on Bullpen, so the slice is non-empty for a real run; a do-nothing run
    # must not bank the task's core migration mechanic on silence.
    new_data_plane = [
        e for e in (first_new_calls + second_new_calls)
        if str(e.get("path", "")).startswith(("/v1/", "/v2/", "/oauth"))
    ]
    if new_data_plane or legacy_calls:
        ctx.check(
            "no_writes_to_legacy_post_cutover",
            len(legacy_calls) == 0,
            f"unexpected vendor-legacy calls during writeback (incl. retry): {len(legacy_calls)}",
            # primary_mechanic (task.yaml): writes must stop reaching the
            # RETIRED vendor once cutover completes -- the starter's
            # writeback targets StaffLine exclusively, so it fails this.
            pass_value=2,
            fail_value=0,
            mandatory=True,
        )

    write_calls = _write_calls(first_new_calls)
    ok_paths = {e.get("path") for e in write_calls if e.get("status") in (200, 201)}
    # Restores the deleted `writeback_result_matches_fixture`, keyed per
    # (op, candidate_id) — writeback_result.json is a flat LIST of write records
    # with no client_ref, so that pair is its natural identity (verified against
    # the fixture: two records, update_candidate and create_note, both for
    # cand_0001). The three checks around this one grade the ROUTING (writes reach
    # Bullpen, none reach the retired vendor, the retry reuses its key); this
    # grades what was actually RECORDED for each write, which is where a connector
    # that routes correctly but records the wrong outcome shows up.
    wb_want = json.loads((ctx.fixtures / "writeback_result.json").read_text(encoding="utf-8"))
    got_writes = out if isinstance(out, list) else []
    want_by_op = {(w.get("op"), w.get("candidate_id")): w for w in wb_want}
    got_by_op = {(w.get("op"), w.get("candidate_id")): w for w in got_writes}
    wb_diffs = []
    for key in sorted(set(want_by_op) | set(got_by_op), key=str):
        w, g = want_by_op.get(key), got_by_op.get(key)
        if g is None:
            wb_diffs.append({"write": key, "field": "<missing write>"})
            continue
        if w is None:
            wb_diffs.append({"write": key, "field": "<unexpected write>"})
            continue
        # The public result contract is exactly these fields. Idempotency-key
        # reuse is independently and more reliably graded from request logs;
        # gold's private output-only key must not reject a contract-faithful
        # result that omits it.
        for field in ("op", "candidate_id", "ok", "id", "err"):
            if w.get(field) != g.get(field):
                wb_diffs.append({"write": key, "field": field,
                                 "want": w.get(field), "got": g.get(field)})
    ctx.check(
        "writeback_writes_fields_exact",
        exit_code == 0 and not wb_diffs,
        _diff_detail("writes", got_writes, wb_want, wb_diffs),
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    ctx.check(
        "writes_land_on_bullpen",
        len(write_calls) > 0 and len(ok_paths) == len(write_calls),
        f"write_calls={len(write_calls)} ok={len(ok_paths)}",
        # The positive half of the routing-exclusivity trap: the starter
        # never issues a single successful write against Bullpen v2.
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    mismatched = [
        path for path, key in first_keys.items()
        if key is None or second_keys.get(path) != key
    ]
    ctx.check(
        "idempotency_key_reused_on_retry",
        bool(first_keys) and not mismatched,
        f"first_keys={first_keys} second_keys={second_keys} mismatched={mismatched}",
        # primary_mechanic (task.yaml): a retried write must reuse its
        # original Idempotency-Key on Bullpen -- the starter has no
        # idempotency-key concept at all (and never calls Bullpen).
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    await builtin_l2(ctx)

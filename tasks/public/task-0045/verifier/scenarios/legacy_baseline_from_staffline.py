"""legacy_baseline_from_staffline -- the ONE read of the migration source.

Fresh stack; the harness always boots every declared vendor at checkpoint 0
(grading_core forces `checkpoint_env=0` for all vendors regardless of
docker-compose.yaml's stated default -- see compose.py's vendor_envs). This
scenario explicitly recreates vendor-legacy at CHECKPOINT=1 before reading
it: StaffLine's whole mutations.staffline.yaml timeline (including
cand_0017's delete, the only seeded tombstone) lands at checkpoint>=1
(state.py's `_apply_mutations`), so a bare checkpoint-0 read would see zero
tombstone history -- nothing to preserve across the cutover, defeating this
task's entire point. Runs `python -m staffline_to_bullpen_migrate
baseline`: sweeps every active candidate/job/application and the full
tombstone feed from StaffLine, writes a staffline-shaped snapshot to
candidates/jobs/applications.json, and caches the tombstone facts to
`.staffline_tombstones.json` for the later cutover.

L1 : app exits 0; candidates/jobs/applications.json match the staffline-
     sourced answer-key fixtures (every active record present, none of the
     tombstoned ids among them).
L3 : tombstones_captured -- `.staffline_tombstones.json` exists and its
     entries match the seeded tombstone fixture exactly (proof the full
     since=0 sweep ran, not an empty/partial one); bullpen_untouched --
     zero requests were made against vendor-new during this phase (the
     baseline read only ever talks to StaffLine).
L2  : builtin conduct gates/soft checks.
"""

from __future__ import annotations

import json

from bench.verifier.io import read_json_output

_ENTITIES = ("candidates", "jobs", "applications")


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

    # See module docstring: the harness always boots vendors at checkpoint 0,
    # so this ONE read of StaffLine must explicitly ask for the checkpoint
    # that carries the seeded tombstone history.
    legacy.recreate(checkpoint=1)

    exit_code, _stdout, stderr = ctx.app.run(["baseline"])

    readable = 0
    for name in _ENTITIES:
        out_path = ctx.output_dir / f"{name}.json"
        out = read_json_output(out_path, timeout_s=15.0 if exit_code == 0 else 0.5)
        if out is None:
            ctx.check(
                f"{name}_output_exists",
                False,
                f"missing/unreadable {out_path.name}",
                pass_value=0,
                fail_value=-1,
                mandatory=False,
            )
            continue
        readable += 1
        # Restores the deleted `{name}_matches_baseline_fixture` compares. 0/-1
        # by MEASUREMENT: the empty probe passes all three (the starter's
        # StaffLine baseline read already works — this task's exercise is the
        # writeback routing after cutover). Must-not-break, earns nothing.
        want = json.loads((ctx.fixtures / f"{name}_baseline.json").read_text(encoding="utf-8"))
        diffs = _row_diff(out, want)
        ctx.check(
            f"{name}_rows_exact_baseline",
            exit_code == 0 and not diffs,
            _diff_detail(f"{name}@baseline", out, want, diffs),
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
    # AND-ed with output evidence (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run; gold always writes
    # all three baseline files.
    ctx.check(
        "app_exit_ok",
        exit_code == 0 and readable > 0,
        f"exit={exit_code} stderr={stderr[:500]} outputs_readable={readable}/{len(_ENTITIES)}",
        # Starter's read_baseline/run_baseline are unchanged/complete (per
        # solution.patch) -- this passes for the do-nothing starter already.
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # ------------------------------------------------------------------- L3
    cache_path = ctx.output_dir / ".staffline_tombstones.json"
    cached = None
    if cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cached = None
    tombstones_fx = json.loads((ctx.fixtures / "staffline_tombstones.json").read_text(encoding="utf-8"))
    # The cache file's shape is the candidate's own implementation detail --
    # nothing in PROBLEM.md dictates it must be a list of {entity, source_id}
    # rows. A candidate that cached a different (still internally consistent)
    # shape must fail *this* check, not crash the whole scenario and wipe out
    # every other check's verdict. Measured 2026-09-03 on task-0045/fable-5.1:
    # a non-dict-row cache raised `TypeError: string indices must be integers`
    # from inside this comparison, which propagated out of run() and zeroed
    # all 23 checks -- including ones that had nothing to do with tombstones.
    try:
        tombstones_match = cached is not None and sorted(
            cached, key=lambda r: (r["entity"], r["source_id"])
        ) == sorted(tombstones_fx, key=lambda r: (r["entity"], r["source_id"]))
    except (TypeError, KeyError):
        tombstones_match = False
    ctx.check(
        "tombstones_captured",
        tombstones_match,
        f"cached={len(cached) if cached is not None else None} fixture={len(tombstones_fx)}",
        # Starter's read_baseline/run_baseline are unchanged/complete -- this
        # already passes for the do-nothing starter (see app_exit_ok above).
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # Evidence gate (mirrors builtin_l2's traffic-conditional rule): "never
    # touched Bullpen" is only meaningful if the baseline phase actually ran —
    # gate on the submission's own StaffLine data-plane traffic (/svc/*; the
    # compose healthcheck's bare "/" pings don't count). Gold's baseline
    # always reads StaffLine, so the slice is non-empty for a real run; a
    # do-nothing run must not bank this prohibition on silence.
    legacy_calls = [e for e in legacy.request_log() if str(e.get("path", "")).startswith("/svc/")]
    if legacy_calls:
        new_calls = [e for e in new.request_log() if str(e.get("path", "")).startswith(("/v1/", "/v2/", "/oauth"))]
        ctx.check(
            "bullpen_untouched",
            len(new_calls) == 0,
            f"unexpected vendor-new calls during baseline: {len(new_calls)}",
            # run_baseline never imports/calls BullpenClient at all -- the
            # starter already satisfies this trivially.
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )

    # NOTE (2026-08-01, lever 1): builtin_l2 grades `ctx.vendor_metadata`
    # (bullpen/vendor-new, per task.yaml's `vendor: bullpen`), which is
    # NEVER recreated anywhere in this task's 3 scenarios — one continuous
    # vendor-lifetime epoch spans this scenario, cutover_backfill_to_bullpen,
    # and writeback_lands_on_new_vendor. Calling builtin_l2 once per
    # scenario (3x) re-graded the SAME accumulating bullpen request log from
    # scratch each time (this scenario's own log is empty by design —
    # bullpen_untouched proves it — so this call was already a no-op here,
    # but the other two calls elsewhere were not). Collapsed to a single
    # call in the LAST scenario (writeback_lands_on_new_vendor.py), which
    # loses no traffic since bullpen is never recreated — see that module
    # for the one remaining invocation.

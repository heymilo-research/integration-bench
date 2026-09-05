"""cutover_backfill_to_bullpen -- the migration's core mechanic.

Continues from wherever legacy_baseline_from_staffline.py left the stack
(StaffLine already swept once; `.staffline_tombstones.json` cached on disk).
Runs `python -m staffline_to_bullpen_migrate migrate`: backfills every
entity from Bullpen v2 (bucket/mixed-timestamp mapping applies) and must
union in the cached StaffLine tombstones, translated into Bullpen's
`is_deleted` flag convention under a `legacy:<entity>:<id>` namespaced id
-- see PROBLEM.md, this is the "without losing tombstoned history"
requirement.

L1 : app exits 0; candidates/jobs/applications.json match the final
     merged answer-key fixtures.
L3 : tombstoned_history_preserved -- every id in the cached tombstones
     file appears in the corresponding output file as
     `legacy:<entity>:<id>`, `is_deleted: true` (proof no StaffLine delete
     was silently dropped when Bullpen took over as the data source);
     migration_parity -- no duplicate source_ids in any output file, and
     every non-legacy (Bullpen-native) row's source_id actually came back
     from a `/v2/*` call this phase (proof of no phantom/fabricated rows);
     no_staffline_calls_during_migrate -- zero requests hit vendor-legacy
     in this phase (the cutover reads only the local tombstone cache, per
     PROBLEM.md's "StaffLine is never called again").
L2  : builtin conduct gates/soft checks.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

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
        if str(sid).startswith("legacy:"):
            # The ticket requires namespaced identity, deletion state and the
            # deletion instant. It does not prescribe gold's private inner
            # payload or whether that instant is emitted as ISO text or epoch
            # milliseconds.
            if g.get("is_deleted") is not True:
                diffs.append({"source_id": sid, "field": "is_deleted",
                              "want": True, "got": g.get("is_deleted")})
            if _timestamp_ms(g.get("updated_at")) != _timestamp_ms(w.get("updated_at")):
                diffs.append({"source_id": sid, "field": "updated_at",
                              "want": w.get("updated_at"), "got": g.get("updated_at")})
            continue
        for key in sorted(set(w) | set(g)):
            if w.get(key) != g.get(key):
                diffs.append({"source_id": sid, "field": key,
                              "want": w.get(key), "got": g.get(key)})
    return diffs


def _timestamp_ms(value):
    """Normalize contract-valid epoch/ISO deletion instants to milliseconds."""
    if isinstance(value, (int, float)):
        number = float(value)
        return round(number if abs(number) >= 100_000_000_000 else number * 1000)
    if isinstance(value, str):
        text = value.strip()
        try:
            return _timestamp_ms(float(text))
        except ValueError:
            pass
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return round(parsed.timestamp() * 1000)
        except ValueError:
            return None
    return None


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

    exit_code, _stdout, stderr = ctx.app.run(["migrate"])

    outputs = {}
    ok_files = True
    for name in _ENTITIES:
        out_path = ctx.output_dir / f"{name}.json"
        out = read_json_output(out_path, timeout_s=15.0 if exit_code == 0 else 0.5)
        outputs[name] = out
        if out is None:
            ctx.check(
                f"{name}_output_exists",
                False,
                f"missing/unreadable {out_path.name}",
                pass_value=0,
                fail_value=-1,
                mandatory=False,
            )
            ok_files = False
            continue
    # AND-ed with output evidence (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run; gold always writes
    # all three merged files.
    readable = sum(v is not None for v in outputs.values())
    ctx.check(
        "app_exit_ok",
        exit_code == 0 and readable > 0,
        f"exit={exit_code} stderr={stderr[:500]} outputs_readable={readable}/{len(_ENTITIES)}",
        # The starter's run_migrate writes a (incomplete) bullpen-only backfill
        # and exits 0 already -- see tombstoned_history_preserved below for
        # the actual trap.
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # ------------------------------------------------------------------- L3
    cache_path = ctx.output_dir / ".staffline_tombstones.json"
    tombstones = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.is_file() else []
    entity_to_file = {"candidate": "candidates", "job": "jobs", "application": "applications"}

    # `tombstones` comes from the candidate's own cache file -- its row shape
    # is the candidate's implementation detail, not something PROBLEM.md
    # dictates. A malformed/differently-shaped row must count as evidence of
    # a lost tombstone (this check is mandatory), not crash the scenario and
    # wipe out every other check's verdict. Measured 2026-09-03 on
    # task-0045/fable-5.1: a non-dict row raised `TypeError: string indices
    # must be integers` from `t["entity"]`, which propagated out of run().
    missing = []
    for t in tombstones:
        if not isinstance(t, dict) or "entity" not in t or "source_id" not in t:
            missing.append(f"<malformed-tombstone-row:{t!r}>")
            continue
        table = entity_to_file.get(t["entity"])
        if table is None:
            continue
        expected_id = f"legacy:{t['entity']}:{t['source_id']}"
        rows = outputs.get(table) or []
        row = next((r for r in rows if isinstance(r, dict) and r.get("source_id") == expected_id), None)
        if row is None or row.get("is_deleted") is not True:
            missing.append(expected_id)
    # Restores the deleted `{name}_matches_final_fixture` compares, split by what
    # each entity can prove. MEASURED: the empty probe FAILS
    # candidates_matches_final_fixture but PASSES the jobs and applications ones
    # — the starter's migration lands those two correctly and breaks on
    # candidates (which is also where the tombstoned history lives, per the check
    # below). All three answer keys genuinely differ from their baselines
    # (150->251, 25->40, 180->300 rows across a different vendor id space —
    # verified), so this is a real split, not an artifact of row order.
    # `exit_code == 0 and` is the inertia guard: all three scenarios share one
    # output dir (see task-0044's entry).
    for name in _ENTITIES:
        out = outputs.get(name)
        want = json.loads((ctx.fixtures / f"{name}_final.json").read_text(encoding="utf-8"))
        diffs = _row_diff(out, want)
        detail = _diff_detail(f"{name}@final", out, want, diffs)
        if name == "candidates":
            ctx.check(f"{name}_rows_exact_final",
                exit_code == 0 and not diffs, detail,
                pass_value=2, fail_value=0, mandatory=True,
            )
        else:
            ctx.check(f"{name}_rows_exact_final",
                exit_code == 0 and not diffs, detail,
                pass_value=0, fail_value=-1, mandatory=False,
            )

    ctx.check(
        "tombstoned_history_preserved",
        not missing and bool(tombstones),
        f"tombstones_checked={len(tombstones)} missing_or_not_deleted={missing[:5]}",
        # Defect #2 (task.yaml comment): starter's run_migrate writes the
        # Bullpen backfill only and never unions in the cached legacy
        # tombstones -- this is the "without losing tombstoned history"
        # requirement PROBLEM.md calls out as the connector's real job.
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    dup_detail = []
    for name in _ENTITIES:
        out = outputs.get(name)
        if not out:
            continue
        ids = [r["source_id"] for r in out if isinstance(r, dict) and "source_id" in r]
        if len(ids) != len(set(ids)):
            seen, dups = set(), set()
            for i in ids:
                (dups if i in seen else seen).add(i)
            dup_detail.append(f"{name} dups={sorted(dups)[:5]}")
    ctx.check(
        "migration_parity",
        not dup_detail and ok_files,
        "; ".join(dup_detail) if dup_detail else "no duplicates",
        # Starter's bullpen-only backfill (no merge) has zero duplicate risk
        # already -- only a botched union (regression) could introduce dups.
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # Exclude bare "/" pings: docker-compose's healthcheck polls
    # http://localhost:8000/ every 2s for the container's entire lifetime,
    # independent of anything the connector does, and lands in the same
    # requests.jsonl this check inspects. Only /svc/* is a real StaffLine
    # data-plane call this connector could have made.
    legacy_calls_during_migrate = [
        e for e in legacy.request_log()
        if e.get("ts", 0) > marker_legacy_ts and str(e.get("path", "")).startswith("/svc/")
    ]
    new_calls_during_migrate = [
        e for e in new.request_log()
        if e.get("ts", 0) > marker_new_ts and str(e.get("path", "")).startswith("/v2/")
    ]
    ctx.check(
        "no_staffline_calls_during_migrate",
        len(legacy_calls_during_migrate) == 0 and len(new_calls_during_migrate) > 0,
        f"legacy_calls={len(legacy_calls_during_migrate)} bullpen_calls={len(new_calls_during_migrate)}",
        # run_migrate already only talks to Bullpen in the starter -- this
        # is a must-not-regress guard, not the trap.
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # builtin_l2 is called ONCE for this task, in the LAST scenario
    # (writeback_lands_on_new_vendor.py) — see the lever-1 note in
    # legacy_baseline_from_staffline.py. vendor-new/bullpen (the vendor
    # builtin_l2 grades) is never recreated across any of this task's 3
    # scenarios, so a single call over its whole accumulated log at the end
    # loses no traffic; calling it here too would just re-grade this
    # scenario's slice of that same log a second time for free.

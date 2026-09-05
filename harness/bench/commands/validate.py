"""`bench validate` — the authoring gauntlet (docs/conduct-rules.md gates 1-3).

Automates:
  1. Gold green   — solution.patch resolves, all `--runs` runs.
  2. Empty patch red — unmodified repo is unresolved, all `--runs` runs.
  2b. Do-nothing floor — the reward is dense now (fraction of verifier checks
                    passed, normalized against a MEASURED gold ceiling and a
                    measured do-nothing floor:
                    `clamp((f - f0) / (fg - f0), 0, 1)`). A do-nothing
                    submission normalizes to exactly 0 *by construction* once
                    f0 is the measured max probe fraction and fg is gold's
                    measured fraction — but that only holds if there's an
                    actual measurement range between them (f0 < fg). Gold is
                    NOT assumed to reach 1.0: the audit sweep found gold
                    solutions legitimately scoring <1.0 (missing a soft
                    conduct check here and there) on several tasks, so the
                    ceiling is whatever gold actually measures, not an
                    assumption. This gate pins: gold resolves (gate 1 already
                    covers that — not re-asserted here), the empty patch is
                    unresolved, and floor_fraction < gold_fraction (a
                    meaningful measurement range exists). It probes with TWO
                    do-nothing submissions: the empty patch (app never runs)
                    and a "stub" patch (app runs, entry point exits 0
                    immediately) — the stub exists because a clean-exit no-op
                    still lets scenarios run to completion and can bank
                    prohibitions that pass vacuously on an empty request log
                    (see bench.commands.stub).
  3. Flake gate   — identical verdicts across the `--runs` repeats, for gold,
                    empty, and stub, respectively (structural equality
                    ignoring run_id).

Gates 4-7 in conduct-rules.md (lie discoverability, docs necessary-but-not-
sufficient, correct-but-different, spec-sufficiency) are human-review gates
by design (the rulebook says so explicitly) — this command does not attempt
to automate them and says so in its report.
"""

from __future__ import annotations

import dataclasses
import json
import math
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bench.commands.grading_core import grade_once
from bench.commands.stub import stub_patch_text
from bench.verdict import verdict_semantic_diff, verdicts_equal_ignoring_run_id

# Schema 2 (2026-08-02): adds `checks_v2`, the per-check three-probe table
# (see build_checks_v2). Additive — every schema-1 field is retained, and
# schema-1 readers keep working.
BASELINE_SCHEMA = 2
MAX_INFRASTRUCTURE_ATTEMPTS = 2


def _grade_probe(
    task_dir: Path,
    patch_path: Path,
    run_id: str,
    *,
    keep: bool,
    workdir_root: Path | None,
):
    """Grade one probe, retrying only benchmark-owned infrastructure faults.

    Candidate patch/build/runtime failures remain scored outcomes. An exhausted
    benchmark-infrastructure retry is invalid evidence and aborts validation
    with CLI exit 2 instead of being folded into the empty/stub floor.
    """
    last = None
    for _attempt in range(MAX_INFRASTRUCTURE_ATTEMPTS):
        last = grade_once(
            task_dir,
            patch_path,
            run_id,
            keep=keep,
            workdir_root=workdir_root,
        )
        if not last.failure_class.startswith("benchmark_"):
            return last
    detail = last.verdict.error if last is not None else "unknown infrastructure failure"
    raise ValueError(
        "validation aborted after benchmark-owned failure retries; "
        f"probe={run_id} attempts={MAX_INFRASTRUCTURE_ATTEMPTS}: {detail}"
    )


# ---------------------------------------------------------------------------
# Scoring helpers (dense reward: fraction of verifier checks passed)
# ---------------------------------------------------------------------------


def _all_checks(verdict_dict: dict[str, Any]) -> list[dict[str, Any]]:
    """Every check dict recorded on a verdict: l1 + l2.hard + l3 + l2.soft.results."""
    l1 = verdict_dict.get("l1", []) or []
    l3 = verdict_dict.get("l3", []) or []
    l2 = verdict_dict.get("l2", {}) or {}
    hard = l2.get("hard", []) or []
    soft_results = (l2.get("soft", {}) or {}).get("results", []) or []
    return [*l1, *hard, *l3, *soft_results]


def check_fraction(verdict_dict: dict[str, Any], n_total: int | None) -> float:
    """Fraction of verifier checks passed (l1 + l2.hard + l3 + l2.soft.results),
    divided by `n_total` (or this verdict's own recorded check count when
    `n_total` is None), capped at 1.0. 0.0 if the denominator is 0.

    Callers grading the gauntlet's fairness gates should pass an explicit
    `n_total` (the gold verdict's check count) for every verdict so probes
    that under-record checks (e.g. because they crashed early) are scored
    against the *complete* check set, not their own truncated one.
    """
    checks = _all_checks(verdict_dict)
    denom = n_total if n_total is not None else len(checks)
    if not denom:
        return 0.0
    passed = sum(1 for c in checks if c.get("ok"))
    return min(passed / denom, 1.0)


def _check_names(verdict_dict: dict[str, Any]) -> dict[str, list[str]]:
    l2 = verdict_dict.get("l2", {}) or {}
    return {
        "l1": [c["name"] for c in verdict_dict.get("l1", []) or []],
        "hard": [c["name"] for c in l2.get("hard", []) or []],
        "l3": [c["name"] for c in verdict_dict.get("l3", []) or []],
        "soft": [c["name"] for c in (l2.get("soft", {}) or {}).get("results", []) or []],
    }


# ---------------------------------------------------------------------------
# Empty-baseline sidecar (do-nothing floor, tasks/<id>/verifier/empty-baseline.json)
# ---------------------------------------------------------------------------


def baseline_sidecar_path(task_dir: Path) -> Path:
    return Path(task_dir) / "verifier" / "empty-baseline.json"


def read_baseline_sidecar(task_dir: Path) -> dict[str, Any] | None:
    path = baseline_sidecar_path(task_dir)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _check_instances(verdict_dict: dict) -> dict[str, list[tuple[str, bool]]]:
    """Every named check instance a verdict recorded, per bucket, in recorded
    order: ``{bucket: [(name, ok), ...]}``."""
    l2 = verdict_dict.get("l2", {}) or {}
    return {
        "l1": [(c["name"], bool(c["ok"])) for c in verdict_dict.get("l1", []) or []],
        "hard": [(c["name"], bool(c["ok"])) for c in l2.get("hard", []) or []],
        "l3": [(c["name"], bool(c["ok"])) for c in verdict_dict.get("l3", []) or []],
        "soft": [
            (c["name"], bool(c["ok"])) for c in (l2.get("soft", {}) or {}).get("results", []) or []
        ],
    }


def build_checks_v2(
    gold_verdict: dict,
    empty_verdict: dict | None,
    stub_verdict: dict | None,
    *,
    positive_check_allowlist: set[str] | None = None,
    mandatory_check_allowlist: set[str] | None = None,
) -> list[dict[str, Any]]:
    """The schema-2 per-check table: one row per GOLD verdict check instance,
    carrying all three probe outcomes — the partition scoring v2 needs
    (SCORING-V2.md §4.1) and which scalar fractions cannot be un-averaged
    back into.

    Gold defines the rows (it runs to completion, so it records the complete
    check set). A probe instance is matched to a row by (bucket, name,
    occurrence index); a probe that never recorded the instance — aborted
    early, scenario never fired — contributes False (unreached = failed,
    same rule submissions are scored by). ``stub`` is None for every row
    when the stub probe was skipped for the task."""

    def occurrences(verdict: dict | None) -> dict[tuple[str, str], list[bool]]:
        table: dict[tuple[str, str], list[bool]] = {}
        if verdict is not None:
            for bucket, instances in _check_instances(verdict).items():
                for name, ok in instances:
                    table.setdefault((bucket, name), []).append(ok)
        return table

    def scoring_of(verdict: dict | None) -> dict[tuple[str, str], list[dict]]:
        """Authored pass_value/fail_value/mandatory per (bucket, name) instance."""
        table: dict[tuple[str, str], list[dict]] = {}
        if verdict is None:
            return table
        l2 = verdict.get("l2", {}) or {}
        groups = {
            "l1": verdict.get("l1", []) or [],
            "hard": l2.get("hard", []) or [],
            "l3": verdict.get("l3", []) or [],
            "soft": (l2.get("soft", {}) or {}).get("results", []) or [],
        }
        for bucket, checks in groups.items():
            for c in checks:
                table.setdefault((bucket, c["name"]), []).append(
                    {
                        "pass_value": int(c.get("pass_value", 1)),
                        "fail_value": int(c.get("fail_value", 0)),
                        "mandatory": bool(c.get("mandatory", False)),
                    }
                )
        return table

    gold_scoring = scoring_of(gold_verdict)
    empty_occ = occurrences(empty_verdict)
    stub_occ = occurrences(stub_verdict)
    cursors: dict[tuple[str, str], int] = {}
    rows: list[dict[str, Any]] = []
    scoring_by_name: dict[str, dict[str, Any]] = {}
    for bucket, instances in _check_instances(gold_verdict).items():
        for name, gold_ok in instances:
            key = (bucket, name)
            idx = cursors.get(key, 0)
            cursors[key] = idx + 1
            empty_hits = empty_occ.get(key, [])
            stub_hits = stub_occ.get(key, [])
            row = {
                "bucket": bucket,
                "name": name,
                "gold": gold_ok,
                "empty": empty_hits[idx] if idx < len(empty_hits) else False,
                "stub": (
                    None
                    if stub_verdict is None
                    else (stub_hits[idx] if idx < len(stub_hits) else False)
                ),
            }
            # Carry the AUTHORED scoring through to the sidecar. Without it,
            # `bench scoring-status` can only reconstruct values from bucket
            # defaults and can never show a migrated task as done — the
            # migration would have no observable finish line.
            scored = gold_scoring.get((bucket, name), [])
            if idx < len(scored):
                row.update(scored[idx])
            # A name is one fixed-universe property. Repeated occurrences may
            # live in different reporting buckets, but cannot silently acquire
            # different weights merely because a legacy helper supplied bucket
            # defaults. Pin every occurrence to the first authored metadata.
            scoring = scoring_by_name.setdefault(
                name,
                {k: row[k] for k in ("pass_value", "fail_value", "mandatory")},
            )
            row.update(scoring)
            # A task-local allowlist lets reworked tasks distinguish actual
            # implementation evidence from checks that merely prove the
            # unchanged starter booted, parsed input, or preserved conduct.
            # Only formerly-positive checks are affected; preserve-style
            # 0/-1 checks keep their authored regression penalty.
            if positive_check_allowlist is not None and int(row["pass_value"]) > 0:
                row["pass_value"] = 1 if name in positive_check_allowlist else 0
            if mandatory_check_allowlist is not None:
                row["mandatory"] = name in mandatory_check_allowlist
            rows.append(row)
    return rows


def read_task_score_policy(task_dir: Path) -> tuple[set[str] | None, dict[str, Any] | None]:
    """Read an optional private per-task positive-check allowlist.

    The policy lives beside the verifier because it is grader logic, not part
    of the participant-facing task.  Its purpose is deliberately narrow: an
    unchanged starter and a plausible documented-only implementation must not
    earn points for plumbing that existed before the requested change.
    """
    path = Path(task_dir) / "verifier" / "task-score-policy.json"
    if not path.is_file():
        return None, None
    try:
        policy = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid task score policy {path}: {exc}") from exc
    if not isinstance(policy, dict) or policy.get("schema") != 1:
        raise ValueError(f"{path}: expected schema 1 object")
    if policy.get("mode") != "positive-check-allowlist":
        raise ValueError(f"{path}: unsupported mode {policy.get('mode')!r}")
    names = policy.get("positive_checks")
    if not isinstance(names, list) or not names or not all(isinstance(name, str) for name in names):
        raise ValueError(f"{path}: positive_checks must be a non-empty string list")
    if len(names) != len(set(names)):
        raise ValueError(f"{path}: positive_checks contains duplicate names")
    mandatory = policy.get("mandatory_checks")
    if (
        not isinstance(mandatory, list)
        or not mandatory
        or not all(isinstance(name, str) for name in mandatory)
    ):
        raise ValueError(f"{path}: mandatory_checks must be a non-empty string list")
    if len(mandatory) != len(set(mandatory)):
        raise ValueError(f"{path}: mandatory_checks contains duplicate names")
    if not set(names) <= set(mandatory):
        raise ValueError(f"{path}: every positive check must also be mandatory")
    return set(names), policy


def fixed_check_fraction(gold_verdict: dict, candidate_verdict: dict) -> float:
    """Pass fraction over gold's fixed check-instance universe.

    Missing candidate instances fail and candidate-only instances are ignored.
    This replaces the legacy ``passed / recorded`` probe fraction everywhere
    that makes a validation or reporting decision.
    """
    rows = build_checks_v2(gold_verdict, candidate_verdict, None)
    if not rows:
        return 0.0
    return sum(bool(row["empty"]) for row in rows) / len(rows)


def build_baseline_sidecar(
    *,
    n_total: int,
    empty_fraction: float,
    stub_fraction: float | None,
    gold_fraction: float,
    checks: dict[str, list[str]],
    checks_v2: list[dict[str, Any]] | None = None,
    generated: str | None = None,
) -> dict[str, Any]:
    """The `verifier/empty-baseline.json` payload shape. `stub_fraction` is
    None when the stub probe was skipped (entry command didn't match the
    `["python", "-m", "<pkg>"]` shape); the floor then rests on the empty
    probe alone. `gold_fraction` is the MEASURED gold ceiling (gold is not
    assumed to reach 1.0 — some gold solutions legitimately miss a soft
    conduct check) and is what the dense reward normalizes against instead of
    a hardcoded 1.0.

    Schema 2 is additive: every schema-1 field is retained; `checks_v2` (the
    per-check three-probe table from ``build_checks_v2``) is what scoring v2
    reads. It is None only for payloads built without probe verdicts (older
    callers/tests) — a real `--write-baseline` pass always records it."""
    floor_fraction = empty_fraction if stub_fraction is None else max(empty_fraction, stub_fraction)
    if generated is None:
        today = datetime.now(timezone.utc).date().isoformat()
        generated = f"bench validate --write-baseline {today}"
    payload: dict[str, Any] = {
        "schema": BASELINE_SCHEMA,
        "n_total": n_total,
        "floor_fraction": floor_fraction,
        "gold_fraction": gold_fraction,
        "probes": {"empty": empty_fraction, "stub": stub_fraction},
        "checks": checks,
        "generated": generated,
    }
    if checks_v2 is not None:
        payload["checks_v2"] = checks_v2
        authored_required = list(
            dict.fromkeys(
                str(row["name"])
                for row in checks_v2
                if row.get("gold") is True and row.get("mandatory") is True
            )
        )
        all_gold = list(
            dict.fromkeys(str(row["name"]) for row in checks_v2 if row.get("gold") is True)
        )
        # Prefer an explicitly authored mandatory subset. Legacy suites whose
        # checks predate that annotation use the equally explicit all-gold
        # contract, never an empty/vacuous required set.
        payload["completion"] = {
            "mode": (
                "authored_mandatory_checks" if authored_required else "all_authored_gold_checks"
            ),
            "required_checks": authored_required or all_gold,
        }
        # Keep the legacy probe fractions above for drift diagnostics, but
        # derive the descriptive starter floor over the same fixed authored
        # universe as Task Score. Candidate-only checks therefore cannot
        # inflate the floor, and duplicate names take their worst outcome.
        by_name: dict[str, dict[str, Any]] = {}
        for row in checks_v2:
            name = str(row["name"])
            current = by_name.get(name)
            if current is None:
                by_name[name] = dict(row)
                continue
            current["gold"] = bool(current.get("gold")) and bool(row.get("gold"))
            current["empty"] = bool(current.get("empty")) and bool(row.get("empty"))
            if current.get("stub") is not None and row.get("stub") is not None:
                current["stub"] = bool(current.get("stub")) and bool(row.get("stub"))

        def fixed_score(probe: str) -> tuple[int | None, float | None]:
            if probe == "stub" and stub_fraction is None:
                return None, None
            raw = 0
            ceiling = 0
            mandatory_failed = False
            has_authored_mandatory = any(
                bool(row.get("gold")) and bool(row.get("mandatory")) for row in by_name.values()
            )
            defaults = {
                "l1": (1, 0),
                "l3": (1, 0),
                "hard": (0, -1),
                "soft": (0, 0),
            }
            for row in by_name.values():
                default_pass, default_fail = defaults.get(str(row.get("bucket", "l1")), (1, 0))
                pass_value = int(row.get("pass_value", default_pass))
                fail_value = int(row.get("fail_value", default_fail))
                if bool(row.get("gold")) and pass_value > 0:
                    ceiling += pass_value
                raw += pass_value if bool(row.get(probe)) else fail_value
                required = (
                    bool(row.get("mandatory")) if has_authored_mandatory else bool(row.get("gold"))
                )
                mandatory_failed = mandatory_failed or (required and not bool(row.get(probe)))
            if ceiling <= 0:
                # Validation will reject this gold manifest. Still return a
                # diagnostic payload instead of masking the intended gate
                # failure with an exception.
                return raw, None
            if mandatory_failed:
                return raw, 0.0
            return raw, 100.0 * min(max(raw, 0), ceiling) / ceiling

        empty_raw, empty_task_score = fixed_score("empty")
        stub_raw, stub_task_score = fixed_score("stub")
        scored_floors = [v for v in (empty_task_score, stub_task_score) if v is not None]
        floor_task_score = max(scored_floors) if scored_floors else None
        payload["scoring"] = {
            "version": "task-score-v4-mandatory-gated",
            "empty_raw": empty_raw,
            "empty_task_score": empty_task_score,
            "stub_raw": stub_raw,
            "stub_task_score": stub_task_score,
            "floor_task_score": floor_task_score,
        }
    return payload


def write_baseline_sidecar(task_dir: Path, data: dict[str, Any]) -> Path:
    path = baseline_sidecar_path(task_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    return path


STRICT_FLOOR_SHARE = 0.40
STRICT_MIN_HEADROOM = 0.25
# For fix/harden tasks the starter is CORRECT BY DESIGN apart from its planted
# defects, so a high floor is the task's premise, not a flaw. The reward is
# normalised — clamp((f - floor)/(gold - floor)) — so the floor's absolute level
# does not shrink the reward range; what determines resolution is how many checks
# actually discriminate. Measured 2026-08-01: task-0043 (0.455 floor, 22 checks,
# ~10 discriminating) and task-0031 (0.556, 18, ~8) give a full 0-1 range with
# adequate granularity, and two attempts to dilute them further were caught as
# false positives and reverted. So those categories are judged on headroom and
# absolute discriminating-check count instead of the floor share.
STRICT_MIN_DISCRIMINATING = 8
STRICT_HEADROOM_ONLY_CATEGORIES = ("fix", "harden")


def _task_category(task_dir: Path) -> str:
    """Declared `category:` (build | fix | harden | migrate | build+harden).
    Best-effort: an unreadable task.yaml falls back to the strict floor rule,
    which is the conservative choice."""
    try:
        import yaml

        data = yaml.safe_load((Path(task_dir) / "task.yaml").read_text(encoding="utf-8")) or {}
        return str(data.get("category", "") or "")
    except Exception:
        return ""


def _partition_counts(checks_v2: list[dict[str, Any]]) -> dict[str, int]:
    """Row counts per v2 scoring class (mirrors the pi package's
    classify_check_v2 — keep the two in sync)."""
    counts = {"discriminating": 0, "preserved": 0, "vacuous": 0, "gold_miss": 0}
    for row in checks_v2:
        if not row.get("gold"):
            cls = "gold_miss"
        elif row.get("stub") is True:
            cls = "vacuous"
        elif row.get("empty"):
            cls = "preserved"
        else:
            cls = "discriminating"
        counts[cls] += 1
    return counts


def strict_gate_passes(
    *,
    floor_fraction: float,
    gold_fraction: float,
    n_total: int,
    category: str,
    checks_v2: list[dict[str, Any]] | None = None,
    stub_skipped: bool = False,
) -> tuple[bool, str]:
    """The 2026-08 hardening bar, category-aware. Returns (passed, rationale).

    With a schema-2 per-check table the discriminating count is the MEASURED
    partition count, not the headroom-derived estimate, and a task with zero
    discriminating checks hard-fails for every category (an unwinnable task —
    SCORING-V2.md §5). The v1 floor-share clause stays in force for
    build/migrate while v1 is the live reward (dual-report vintage); it
    retires at the v2 cutover."""
    # An unmeasured stub probe is not a passing stub probe. `floor` is defined
    # as max(empty, stub); when the stub was skipped (entry command didn't match
    # a shape bench.commands.stub can patch) the floor is only a lower bound, so
    # certifying against it would be certifying a number we never measured.
    #
    # This silently degraded the 200-task holdout expansion: every one of those
    # tasks declared `entry.command: [python, main.py]` (or a `node`/`go`/`java`
    # equivalent), none of which `_entry_package` accepts, so the stub probe was
    # skipped 200/200 while gate 2b still reported PASS. Fail loudly instead.
    if stub_skipped:
        return False, (
            f"category={category or 'build'}: stub probe SKIPPED — floor "
            f"{floor_fraction:.3f} is an empty-only lower bound, not max(empty, stub). "
            "Give the task a stub-patchable entry point (`python -m <pkg>` over a "
            "`repo/src/<pkg>/__main__.py`) and re-run"
        )
    headroom = gold_fraction - floor_fraction
    discriminating = round(headroom * n_total)
    measured = ""
    if checks_v2:
        counts = _partition_counts(checks_v2)
        discriminating = counts["discriminating"]
        measured = (
            f" [measured partition: {counts['discriminating']} discriminating,"
            f" {counts['preserved']} preserved, {counts['vacuous']} vacuous,"
            f" {counts['gold_miss']} gold-miss]"
        )
        if discriminating == 0:
            return (
                False,
                f"category={category or 'build'}: 0 discriminating checks — unwinnable{measured}",
            )
    if category in STRICT_HEADROOM_ONLY_CATEGORIES:
        ok = headroom >= STRICT_MIN_HEADROOM and discriminating >= STRICT_MIN_DISCRIMINATING
        return ok, (
            f"category={category}: headroom {headroom:.3f}>={STRICT_MIN_HEADROOM} and "
            f"{discriminating} discriminating checks>={STRICT_MIN_DISCRIMINATING}{measured}"
        )
    ok = floor_fraction <= STRICT_FLOOR_SHARE * gold_fraction and headroom >= STRICT_MIN_HEADROOM
    return ok, (
        f"category={category or 'build'}: floor {floor_fraction:.3f}<="
        f"{STRICT_FLOOR_SHARE}*gold and headroom {headroom:.3f}>={STRICT_MIN_HEADROOM}{measured}"
    )


def empty_probe_is_red(verdict: dict[str, Any]) -> bool:
    """Use the verdict's completion policy across every check bucket."""
    return not bool(verdict.get("resolved"))


def gate_2b_passes(*, empty_red: bool, floor_fraction: float, gold_fraction: float) -> bool:
    """The strengthened gate 2 (do-nothing floor), isolated as a pure function
    of already-computed fractions so it's testable without grading anything.

    ``empty_red`` is gate 2's canonical verdict assertion: the unmodified
    repository must remain unresolved. This includes failures recorded in L3
    or hard-conduct checks; webhook-heavy tasks can put their main
    discriminators in L3 while all L1 rejection controls correctly pass.
    Gold being RESOLVED on every run is gate 1's job and is deliberately not
    re-asserted here — gold is NOT
    required to reach fraction 1.0 (the measured sweep found gold solutions
    legitimately missing a soft conduct check here and there). What's
    required is `floor_fraction < gold_fraction`: a genuine measurement range
    between the do-nothing floor and the measured gold ceiling, since the
    dense reward normalizes as `clamp((f - f0) / (fg - f0), 0, 1)` and that's
    only meaningful (denominator nonzero, gold itself normalizing above 0) if
    the floor sits strictly below gold.
    """
    return bool(empty_red and floor_fraction < gold_fraction)


def baseline_drift(existing: dict[str, Any], current: dict[str, float | None]) -> list[str]:
    """Keys whose freshly recomputed value differs from what's stored in an
    existing sidecar. `current` maps a field name to its freshly measured
    value: "empty" and "stub" are looked up under the sidecar's `probes`
    object, while "gold_fraction" (and any other top-level field) is looked
    up at the sidecar's top level. A value of None this run (e.g. the stub
    probe was skipped) or a key absent from the stored sidecar is not
    comparable and is silently excluded rather than counted as drift."""
    stored_probes = existing.get("probes", {}) or {}
    drifted = []
    for name, value in current.items():
        if value is None:
            continue
        stored = stored_probes.get(name) if name in ("empty", "stub") else existing.get(name)
        if stored is None:
            continue
        if not math.isclose(value, stored, rel_tol=1e-9, abs_tol=1e-9):
            drifted.append(name)
    return drifted


def baseline_artifact_drift(existing: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """Fail closed when the committed authored manifest differs from probes.

    Scalar probe drift alone cannot detect renamed, omitted, reweighted, or
    reclassified checks whose aggregate fraction happens to stay unchanged.
    These fields are the actual fixed-gold scoring and completion contract and
    therefore require exact reproduction (apart from the provenance date).
    """
    fields = ("n_total", "checks", "checks_v2", "completion", "scoring")
    drifted: list[str] = []
    for name in fields:
        if name not in existing:
            drifted.append(f"{name}:missing")
        elif existing[name] != current.get(name):
            drifted.append(name)
    return drifted


@dataclasses.dataclass
class ValidateReport:
    task_id: str
    runs: int
    gold_green: bool
    empty_red: bool
    flake_gate: bool
    flake_drift: list[str]
    gold_verdicts: list[dict[str, Any]]
    empty_verdicts: list[dict[str, Any]]
    stub_verdicts: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    stub_skipped: bool = False
    n_total: int = 0
    gold_fraction: float = 0.0
    empty_fraction: float = 0.0
    stub_fraction: float | None = None
    floor_fraction: float = 0.0
    gate_2b: bool = False
    baseline: dict[str, Any] | None = None
    baseline_written: bool = False
    baseline_drift: list[str] = dataclasses.field(default_factory=list)
    strict: bool = False
    strict_gate: bool | None = None
    strict_rationale: str = ""

    @property
    def passed(self) -> bool:
        gates = self.gold_green and self.empty_red and self.flake_gate and self.gate_2b
        if self.strict:
            gates = gates and bool(self.strict_gate)
        if self.baseline_drift:
            return False
        return gates

    def render(self) -> str:
        stub_display = "skipped" if self.stub_skipped else f"{self.stub_fraction:.3f}"
        sidecar_note = ""
        if self.baseline_written:
            sidecar_note = " (sidecar written)"
        elif self.baseline_drift:
            sidecar_note = f" (DRIFT vs stored baseline: {', '.join(self.baseline_drift)})"

        lines = [
            f"Validation gauntlet: {self.task_id}  ({self.runs} runs each)",
            "",
            f"  [{'PASS' if self.gold_green else 'FAIL'}] gate 1 — gold green "
            f"(solution.patch resolves {self.runs}/{self.runs})",
            f"  [{'PASS' if self.empty_red else 'FAIL'}] gate 2 — empty patch red "
            f"(unresolved, {self.runs}/{self.runs})",
            f"  [{'PASS' if self.gate_2b else 'FAIL'}] gate 2b — floor: "
            f"empty={self.empty_fraction:.3f} stub={stub_display} "
            f"gold={self.gold_fraction:.3f} n={self.n_total}{sidecar_note}",
        ]
        probe_errors: list[str] = []
        for probe, verdicts in (
            ("gold", self.gold_verdicts),
            ("empty", self.empty_verdicts),
            ("stub", self.stub_verdicts),
        ):
            for verdict in verdicts:
                error = verdict.get("error")
                if error:
                    message = f"  probe error [{probe}]: {error}"
                    if message not in probe_errors:
                        probe_errors.append(message)
        lines.extend(probe_errors)
        if self.strict:
            lines.append(
                f"  [{'PASS' if self.strict_gate else 'FAIL'}] strict gate — "
                f"{self.strict_rationale or 'hardening bar'} "
                f"(floor={self.floor_fraction:.3f} gold={self.gold_fraction:.3f} n={self.n_total})"
            )
        if self.stub_skipped:
            lines.append("  stub: skipped (entry shape)")
        if self.gold_fraction < 1.0:
            lines.append(
                f"  note: gold_fraction {self.gold_fraction:.3f} < 1.0 (gold misses a soft "
                "conduct check somewhere) — informational, does not block validation"
            )
        lines.extend(
            [
                f"  [{'PASS' if self.flake_gate else 'FAIL'}] gate 3 — flake gate "
                f"(identical verdicts across {self.runs} runs, gold/empty/stub)",
            ]
        )
        lines.extend(f"    drift: {item}" for item in self.flake_drift)
        lines.extend(
            [
                "",
                "  gates 4-7 (lie discoverability, docs necessary-but-not-sufficient,",
                "  correct-but-different, spec-sufficiency) are human-review gates per",
                "  docs/conduct-rules.md and are not automated by this command.",
                "",
                f"Overall: {'PASS' if self.passed else 'FAIL'}",
            ]
        )
        return "\n".join(lines)


def _patch_file(text: str, suffix: str) -> Path:
    fd = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False)
    fd.write(text)
    fd.close()
    return Path(fd.name)


def _empty_patch_file() -> Path:
    return _patch_file("", ".empty.patch")


def validate_task(
    task_dir: Path,
    *,
    runs: int = 5,
    keep: bool = False,
    workdir_root: Path | None = None,
    write_baseline: bool = False,
    strict: bool = False,
) -> ValidateReport:
    task_dir = Path(task_dir)
    solution_patch = task_dir / "authoring" / "solution.patch"
    if not solution_patch.is_file():
        raise FileNotFoundError(f"no solution.patch under {task_dir}")

    empty_patch = _empty_patch_file()

    stub_text = stub_patch_text(task_dir)
    stub_skipped = stub_text is None
    stub_patch = _patch_file(stub_text, ".stub.patch") if stub_text is not None else None

    gold_verdicts: list[dict[str, Any]] = []
    empty_verdicts: list[dict[str, Any]] = []
    stub_verdicts: list[dict[str, Any]] = []
    try:
        for i in range(runs):
            run_id = f"validate-gold-{uuid.uuid4().hex[:8]}-{i}"
            result = _grade_probe(
                task_dir, solution_patch, run_id, keep=keep, workdir_root=workdir_root
            )
            gold_verdicts.append(result.verdict.to_dict())

        for i in range(runs):
            run_id = f"validate-empty-{uuid.uuid4().hex[:8]}-{i}"
            result = _grade_probe(
                task_dir, empty_patch, run_id, keep=keep, workdir_root=workdir_root
            )
            empty_verdicts.append(result.verdict.to_dict())

        if stub_patch is not None:
            for i in range(runs):
                run_id = f"validate-stub-{uuid.uuid4().hex[:8]}-{i}"
                result = _grade_probe(
                    task_dir, stub_patch, run_id, keep=keep, workdir_root=workdir_root
                )
                stub_verdicts.append(result.verdict.to_dict())
    finally:
        empty_patch.unlink(missing_ok=True)
        if stub_patch is not None:
            stub_patch.unlink(missing_ok=True)

    gold_green = all(v["resolved"] for v in gold_verdicts)
    empty_red = all(empty_probe_is_red(verdict) for verdict in empty_verdicts)

    flake_drift: list[str] = []
    for probe, verdicts in (
        ("gold", gold_verdicts),
        ("empty", empty_verdicts),
        ("stub", stub_verdicts),
    ):
        if not verdicts:
            continue
        first = verdicts[0]
        for index, verdict in enumerate(verdicts[1:], start=1):
            if verdicts_equal_ignoring_run_id(first, verdict):
                continue
            details = verdict_semantic_diff(first, verdict, limit=8)
            flake_drift.extend(f"{probe} run 0 vs {index}: {detail}" for detail in details)
    flake_gate = not flake_drift

    # n_total is pinned to the FIRST GOLD verdict's check count: gold runs to
    # completion so it carries the complete check set, and grading every
    # probe against that same denominator (rather than each probe's own,
    # possibly-truncated, recorded count) is what makes the fractions
    # comparable.
    n_total = len(_all_checks(gold_verdicts[0])) if gold_verdicts else 0
    gold_fraction = (
        fixed_check_fraction(gold_verdicts[0], gold_verdicts[0]) if gold_verdicts else 0.0
    )
    empty_fraction = (
        fixed_check_fraction(gold_verdicts[0], empty_verdicts[0])
        if gold_verdicts and empty_verdicts
        else 0.0
    )
    stub_fraction = (
        fixed_check_fraction(gold_verdicts[0], stub_verdicts[0])
        if gold_verdicts and stub_verdicts
        else None
    )

    floor_fraction = empty_fraction if stub_fraction is None else max(empty_fraction, stub_fraction)

    gate_2b = gate_2b_passes(
        empty_red=empty_red, floor_fraction=floor_fraction, gold_fraction=gold_fraction
    )

    # Strict gate (2026-08 hardening bar, opt-in): a reworked task must not
    # hand the starter/stub a large share of gold, and must leave real
    # discriminative headroom. Opt-in so baseline re-measurement of
    # not-yet-reworked tasks isn't blocked by the new bar.
    positive_check_allowlist, task_score_policy = read_task_score_policy(task_dir)
    mandatory_check_allowlist = (
        set(map(str, task_score_policy["mandatory_checks"]))
        if task_score_policy is not None
        else None
    )
    checks_v2 = (
        build_checks_v2(
            gold_verdicts[0],
            empty_verdicts[0] if empty_verdicts else None,
            stub_verdicts[0] if stub_verdicts else None,
            positive_check_allowlist=positive_check_allowlist,
            mandatory_check_allowlist=mandatory_check_allowlist,
        )
        if gold_verdicts
        else []
    )

    checks_names = (
        _check_names(gold_verdicts[0])
        if gold_verdicts
        else {"l1": [], "hard": [], "l3": [], "soft": []}
    )
    baseline_payload = build_baseline_sidecar(
        n_total=n_total,
        empty_fraction=empty_fraction,
        stub_fraction=stub_fraction,
        gold_fraction=gold_fraction,
        checks=checks_names,
        checks_v2=checks_v2,
    )
    gold_probe_errored = any(bool(verdict.get("error")) for verdict in gold_verdicts)
    if task_score_policy is not None and not gold_probe_errored:
        emitted_names = {str(row["name"]) for row in checks_v2}
        missing_policy_names = sorted(
            (positive_check_allowlist | mandatory_check_allowlist) - emitted_names
        )
        if missing_policy_names:
            raise ValueError(
                f"{task_dir}/verifier/task-score-policy.json names checks absent from gold: "
                + ", ".join(missing_policy_names)
            )

    strict_gate: bool | None = None
    strict_rationale = ""
    if strict:
        # Strict headroom is a scoring-quality gate, so its floor must use the
        # authored fixed-gold Task Score rather than the legacy fraction of
        # passing check instances. Preserve/conduct penalties can make those
        # values materially different (task-0013: 0.378 instances, 0.0 Task
        # Score), and candidate-only checks must never affect this decision.
        floor_task_score = baseline_payload["scoring"]["floor_task_score"]
        if floor_task_score is None:
            strict_gate = False
            strict_rationale = "fixed-gold scoring unavailable (no positive gold ceiling)"
        else:
            strict_floor = float(floor_task_score) / 100.0
            strict_gate, strict_rationale = strict_gate_passes(
                floor_fraction=strict_floor,
                gold_fraction=1.0,
                n_total=n_total,
                category=_task_category(task_dir),
                checks_v2=checks_v2,
                stub_skipped=stub_text is None,
            )

    baseline_written = False
    drift: list[str] = []
    gates_so_far = gold_green and empty_red and flake_gate and gate_2b
    if write_baseline:
        if gates_so_far:
            write_baseline_sidecar(task_dir, baseline_payload)
            baseline_written = True
    else:
        existing = read_baseline_sidecar(task_dir)
        if existing is not None:
            drift = baseline_drift(
                existing,
                {"empty": empty_fraction, "stub": stub_fraction, "gold_fraction": gold_fraction},
            )
            drift.extend(baseline_artifact_drift(existing, baseline_payload))

    task_id = gold_verdicts[0]["task"] if gold_verdicts else task_dir.name
    return ValidateReport(
        task_id=task_id,
        runs=runs,
        gold_green=gold_green,
        empty_red=empty_red,
        flake_gate=flake_gate,
        flake_drift=flake_drift,
        gold_verdicts=gold_verdicts,
        empty_verdicts=empty_verdicts,
        stub_verdicts=stub_verdicts,
        stub_skipped=stub_skipped,
        n_total=n_total,
        gold_fraction=gold_fraction,
        empty_fraction=empty_fraction,
        stub_fraction=stub_fraction,
        floor_fraction=floor_fraction,
        gate_2b=gate_2b,
        baseline=baseline_payload,
        baseline_written=baseline_written,
        baseline_drift=drift,
        strict=strict,
        strict_gate=strict_gate,
        strict_rationale=strict_rationale,
    )

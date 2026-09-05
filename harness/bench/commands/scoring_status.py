"""`bench scoring-status` — how far the per-test scoring migration has got.

Item 1 of the scoring rework ("audit all tests in the public 50, assign value +
mandatory") is a 50-task manual pass. Without a progress readout it is invisible
work with no finish line. This command gives it one:

    every task's gold verdict satisfies validate_scoring()

It reads each task's `verifier/empty-baseline.json` (schema 2), reconstructs the
gold verdict from `checks_v2`, and reports what is still unauthored. No Docker,
no grading — it is a read over sidecars, so it is cheap enough to run after
every task.

Caveat worth stating: a sidecar records the check NAMES and probe outcomes, not
the `pass_value`/`mandatory` the scenario source now carries. Until sidecars are
regenerated on the new schema, this reconstructs scoring from the legacy bucket
defaults, so it measures *structure* (missing mandatory set, bucket collisions,
repeats) and not the authored values themselves. It will read the real values
once sidecars are rewritten.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from bench.scoring import all_checks, dedupe, validate_scoring
from bench.verdict import Check, Verdict

#: Same table as CheckRecorder's legacy shims — see checks.py.
_LEGACY_DEFAULTS = {"l1": (1, 0), "l3": (1, 0), "hard": (0, -1), "soft": (0, 0)}


@dataclasses.dataclass
class TaskScoringStatus:
    task: str
    n_recorded: int
    n_deduped: int
    max_score: int
    n_mandatory: int
    ok: bool
    errors: list[str]
    warnings: list[str]


def _gold_from_sidecar(data: dict) -> Verdict | None:
    rows = data.get("checks_v2")
    if not rows:
        return None
    completion = data.get("completion") or {}
    required_rows = completion.get("required_checks")
    required = (
        set(required_rows)
        if isinstance(required_rows, list)
        else {row["name"] for row in rows if row.get("mandatory") is True}
    )
    v = Verdict(task="t", run_id="r")
    for row in rows:
        bucket = row.get("bucket", "l1")
        # Prefer the AUTHORED values when the sidecar carries them (schema 2+,
        # written after 2026-08-07). Fall back to bucket defaults only for
        # sidecars measured before the scoring refactor.
        default_pass, default_fail = _LEGACY_DEFAULTS.get(bucket, (1, 0))
        pass_value = int(row.get("pass_value", default_pass))
        fail_value = int(row.get("fail_value", default_fail))
        target = getattr(v, bucket, None)
        if target is None:
            continue
        target.append(
            Check(
                name=row["name"],
                ok=bool(row.get("gold")),
                pass_value=pass_value,
                fail_value=fail_value,
                mandatory=row["name"] in required,
            )
        )
    return v


def scoring_status(tasks_dir: Path) -> list[TaskScoringStatus]:
    out: list[TaskScoringStatus] = []
    for task_dir in sorted(tasks_dir.glob("task-*")):
        sidecar = task_dir / "verifier" / "empty-baseline.json"
        if not sidecar.is_file():
            out.append(
                TaskScoringStatus(
                    task=task_dir.name,
                    n_recorded=0,
                    n_deduped=0,
                    max_score=0,
                    n_mandatory=0,
                    ok=False,
                    errors=["no verifier/empty-baseline.json"],
                    warnings=[],
                )
            )
            continue
        try:
            data = json.loads(sidecar.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            out.append(
                TaskScoringStatus(
                    task=task_dir.name,
                    n_recorded=0,
                    n_deduped=0,
                    max_score=0,
                    n_mandatory=0,
                    ok=False,
                    errors=[f"unreadable sidecar: {exc}"],
                    warnings=[],
                )
            )
            continue
        gold = _gold_from_sidecar(data)
        if gold is None:
            out.append(
                TaskScoringStatus(
                    task=task_dir.name,
                    n_recorded=0,
                    n_deduped=0,
                    max_score=0,
                    n_mandatory=0,
                    ok=False,
                    errors=["sidecar has no checks_v2 (schema 1) — re-measure"],
                    warnings=[],
                )
            )
            continue
        recorded = all_checks(gold)
        rep = validate_scoring(task_dir.name, gold)
        out.append(
            TaskScoringStatus(
                task=task_dir.name,
                n_recorded=len(recorded),
                n_deduped=len(dedupe(recorded)),
                max_score=rep.max_score,
                n_mandatory=rep.n_mandatory,
                ok=rep.ok,
                errors=rep.errors,
                warnings=rep.warnings,
            )
        )
    return out


def render(rows: list[TaskScoringStatus], verbose: bool = False) -> str:
    lines = [
        f"{'task':<12} {'checks':>7} {'dedup':>6} {'max':>5} {'mand':>5}  status",
        "-" * 60,
    ]
    for r in rows:
        status = "PASS" if r.ok else "FAIL"
        lines.append(
            f"{r.task:<12} {r.n_recorded:>7} {r.n_deduped:>6} "
            f"{r.max_score:>5} {r.n_mandatory:>5}  {status}"
        )
        if verbose or not r.ok:
            for e in r.errors:
                lines.append(f"{'':<12}   ERROR {e}")
        if verbose:
            for w in r.warnings:
                lines.append(f"{'':<12}   warn  {w}")

    n = len(rows)
    passed = sum(1 for r in rows if r.ok)
    collapsed = sum(r.n_recorded - r.n_deduped for r in rows)
    total = sum(r.n_recorded for r in rows)
    unauthored = sum(1 for r in rows if r.n_mandatory == 0)
    lines += [
        "-" * 60,
        f"{passed}/{n} tasks pass validate_scoring",
        f"{unauthored}/{n} tasks have NO mandatory checks",
        f"{collapsed}/{total} check instances collapse under name dedupe"
        + (f" ({100 * collapsed / total:.1f}%)" if total else ""),
    ]
    return "\n".join(lines)

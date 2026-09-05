"""`bench grade-all` — iterate every task-* dir under tasks/, grade a named
patch (default: predictions/<task>/<run-id>.patch if present, else
solution.patch as a sanity default) against each, appending one verdict JSON
line per task to a results.jsonl aggregate."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Callable

from bench.commands.grading_core import grade_once
from bench.commands.run_all import discover_task_dirs
from bench.scoring import SCORER_VERSION

PatchResolver = Callable[[Path], "Path | None"]


def grade_all(
    tasks_root: Path,
    patch_resolver: PatchResolver,
    *,
    results_path: Path,
    keep: bool = False,
    workdir_root: Path | None = None,
) -> list[dict]:
    task_dirs = discover_task_dirs(tasks_root)
    results = []
    results_path = Path(results_path)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with results_path.open("w", encoding="utf-8") as fh:
        for task_dir in task_dirs:
            run_id = f"grade-{task_dir.name}-{uuid.uuid4().hex[:8]}"
            try:
                patch_path = patch_resolver(task_dir)
                if patch_path is None:
                    raise FileNotFoundError(
                        f"no patch resolved for {task_dir.name} (checked predictions/ "
                        "and solution.patch)"
                    )
                result = grade_once(
                    task_dir, patch_path, run_id, keep=keep, workdir_root=workdir_root
                )
                record = result.verdict.to_dict()
                record.update(
                    {
                        "raw_score": result.raw_score,
                        "task_score": result.task_score,
                        "check_coverage": result.check_coverage,
                        "missing_checks": result.missing_checks,
                        "scorer_version": SCORER_VERSION,
                        "failure_class": result.failure_class,
                    }
                )
            except Exception as exc:  # noqa: BLE001
                record = {
                    "schema_version": 1,
                    "task": task_dir.name,
                    "run_id": run_id,
                    "resolved": False,
                    "l1": [],
                    "l2": {
                        "hard": [],
                        "soft": {"violations": 0, "checks": 0, "score": 1.0, "results": []},
                    },
                    "l3": [],
                    "error": str(exc),
                }
            results.append(record)
            fh.write(json.dumps(record) + "\n")
            fh.flush()
    return results


def default_patch_resolver(predictions_root: Path | None) -> PatchResolver:
    def resolve(task_dir: Path) -> Path | None:
        if predictions_root is not None:
            task_pred_dir = Path(predictions_root) / task_dir.name
            if task_pred_dir.is_dir():
                patches = sorted(task_pred_dir.glob("*.patch"))
                if patches:
                    return patches[-1]  # most recent by name (uuid-suffixed run-id)
        solution = task_dir / "authoring" / "solution.patch"
        return solution if solution.is_file() else None

    return resolve

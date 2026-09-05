"""`bench run-all` — iterate every task-* dir under tasks/ and roll out the
same agent command against each, appending one JSON line per task to a
results.jsonl aggregate."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from bench.commands.rollout_core import run_once


def discover_task_dirs(tasks_root: Path) -> list[Path]:
    tasks_root = Path(tasks_root)
    if not tasks_root.is_dir():
        return []
    return sorted(p for p in tasks_root.iterdir() if p.is_dir() and (p / "task.yaml").is_file())


def run_all(
    tasks_root: Path,
    agent_cmd: str,
    *,
    results_path: Path,
    keep: bool = False,
    workdir_root: Path | None = None,
    predictions_dir: Path | None = None,
) -> list[dict]:
    task_dirs = discover_task_dirs(tasks_root)
    results = []
    results_path = Path(results_path)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with results_path.open("w", encoding="utf-8") as fh:
        for task_dir in task_dirs:
            run_id = f"run-{task_dir.name}-{uuid.uuid4().hex[:8]}"
            try:
                result = run_once(
                    task_dir,
                    agent_cmd,
                    run_id,
                    keep=keep,
                    workdir_root=workdir_root,
                    predictions_dir=predictions_dir,
                )
                record = {
                    "task": result.task_id,
                    "run_id": result.run_id,
                    "eval_id": result.eval_id,
                    "patch_path": str(result.patch_path) if result.patch_path else None,
                    "run_manifest_path": str(result.run_manifest_path),
                    "timed_out": result.timed_out,
                    "agent_returncode": result.agent_returncode,
                    "error": None,
                }
            except Exception as exc:  # noqa: BLE001
                record = {
                    "task": task_dir.name,
                    "run_id": run_id,
                    "eval_id": None,
                    "patch_path": None,
                    "run_manifest_path": None,
                    "timed_out": False,
                    "agent_returncode": None,
                    "error": str(exc),
                }
            results.append(record)
            fh.write(json.dumps(record) + "\n")
            fh.flush()
    return results

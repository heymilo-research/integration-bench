"""Host-command rollout against the canonical standalone-vendor runtime."""

from __future__ import annotations

import dataclasses
import hashlib
import os
import re
import subprocess
import time
from pathlib import Path

from bench.agent_vendors import agent_env_from_stack, start_agent_vendors
from bench.compose import ParticipantResourceError
from bench.config import load_task_config
from bench.eval_output import EvalDir, new_eval_id
from bench.provenance import capture_provenance
from bench.workspace import extract_diff, prepare_rollout_workspace


_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclasses.dataclass
class RunResult:
    task_id: str
    run_id: str
    eval_id: str
    patch_text: str
    patch_path: Path | None
    run_manifest_path: Path
    timed_out: bool
    agent_returncode: int | None
    workspace_dir: Path


def _validate_run_id(run_id: str) -> str:
    if not _RUN_ID_RE.fullmatch(run_id or ""):
        raise ValueError("run_id must contain only letters, digits, dot, underscore, or hyphen")
    return run_id


def _create_run_dir(root: Path, run_id: str) -> EvalDir:
    run_root = Path(root) / _validate_run_id(run_id)
    run_root.mkdir(parents=True, exist_ok=False)
    for relative in ("workspace", "vendor-logs", "canonical-data", "participant-state"):
        (run_root / relative).mkdir()
    attempt_id = new_eval_id()
    ed = EvalDir(root=run_root, eval_id=attempt_id)
    ed.write_meta(
        {
            "eval_id": attempt_id,
            "attempt_id": attempt_id,
            "logical_rollout_id": run_id,
            "run_id": run_id,
            "status": "created",
            "created_at": time.time(),
        }
    )
    return ed


def run_once(
    task_dir: Path,
    agent_cmd: str,
    run_id: str,
    *,
    keep: bool = False,
    workdir_root: Path | None = None,
    predictions_dir: Path | None = None,
    startup_timeout_s: float = 120.0,
) -> RunResult:
    task_dir = Path(task_dir)
    task = load_task_config(task_dir)
    root = Path(workdir_root) if workdir_root else Path("artifacts") / "runs"
    ed = _create_run_dir(root, run_id)
    command_hash = hashlib.sha256(agent_cmd.encode()).hexdigest()

    try:
        provenance = capture_provenance(
            task_dir,
            model="external-agent-command",
            provider="local-command",
            mode="run",
        )
    except Exception as exc:
        fallback = capture_provenance(
            task_dir,
            model="external-agent-command",
            provider="local-command",
            mode="run",
            resolve_images=False,
        )
        ed.write_meta(
            {
                "status": "error",
                "task": task.id,
                "model": "external-agent-command",
                "provider": "local-command",
                "harness": "run",
                "mode": "run",
                "provenance": fallback,
                "agent_command_sha256": command_hash,
                "failure_class": "benchmark_infrastructure_failure",
                "error": str(exc),
            }
        )
        raise

    ed.write_meta(
        {
            "status": "running",
            "task": task.id,
            "model": "external-agent-command",
            "provider": "local-command",
            "harness": "run",
            "mode": "run",
            "provenance": provenance,
            "agent_command_sha256": command_hash,
            "timeout_minutes": task.timeout_minutes,
        }
    )
    repo_dir = prepare_rollout_workspace(task_dir, ed.workspace)

    try:
        stack = start_agent_vendors(
            task_dir,
            project=ed.eval_id,
            startup_timeout_s=startup_timeout_s,
            eval_dir=ed,
        )
    except Exception as exc:
        ed.write_meta(
            {
                "status": "error",
                "failure_class": "benchmark_infrastructure_failure",
                "error": str(exc),
            }
        )
        raise

    timed_out = False
    returncode: int | None = None
    runtime_error: Exception | None = None
    resource_error: ParticipantResourceError | None = None
    started = time.time()
    try:
        env = os.environ.copy()
        env.update(agent_env_from_stack(task_dir, stack))
        try:
            proc = subprocess.run(
                agent_cmd,
                shell=True,
                cwd=ed.workspace,
                env=env,
                timeout=task.timeout_minutes * 60,
            )
            returncode = proc.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
        except Exception as exc:  # preserve a manifest before re-raising
            runtime_error = exc
        try:
            stack.assert_participant_disk_budget()
        except ParticipantResourceError as exc:
            resource_error = exc
    finally:
        try:
            ed.write_vendor_log(stack.logs("vendors"))
        except Exception:
            pass
        if not keep:
            try:
                stack.down()
            except Exception:
                pass
        stack.cleanup_override_file()

    ed.scrub_runtime_secrets(ed.workspace)
    patch_text = "" if ed.secret_leak_detected else extract_diff(repo_dir)
    if ed.secret_leak_detected:
        ed.append_agent_log(
            "runtime secret exfiltration detected; retained workspace was redacted "
            "and the candidate patch was rejected\n"
        )
    ed.write_patch(patch_text)
    patch_path = None
    if predictions_dir is not None:
        pred_dir = Path(predictions_dir) / task.id
        pred_dir.mkdir(parents=True, exist_ok=True)
        patch_path = pred_dir / f"{run_id}.patch"
        patch_path.write_text(patch_text, encoding="utf-8")

    failure_class = (
        "benchmark_infrastructure_failure"
        if runtime_error is not None
        else "candidate_runtime_failure"
        if timed_out or resource_error is not None or returncode not in (None, 0)
        else "ungraded_rollout"
    )
    ed.write_meta(
        {
            "status": "error" if runtime_error else "done",
            "timed_out": timed_out,
            "agent_returncode": returncode,
            "elapsed_s": time.time() - started,
            "failure_class": failure_class,
            "error": str(runtime_error or resource_error)
            if runtime_error or resource_error
            else None,
            "resolved": None,
            "task_score": None,
            "reward": None,
        }
    )
    if runtime_error is not None:
        raise runtime_error

    return RunResult(
        task_id=task.id,
        run_id=run_id,
        eval_id=ed.eval_id,
        patch_text=patch_text,
        patch_path=patch_path,
        run_manifest_path=ed.run_manifest_path,
        timed_out=timed_out,
        agent_returncode=returncode,
        workspace_dir=ed.workspace,
    )

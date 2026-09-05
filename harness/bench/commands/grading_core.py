"""Shared grading engine for `bench grade` and `bench validate` (M1 contract).

Grade lifecycle:
  pristine repo/ copy -> git apply patch (failure = unresolved verdict) ->
  fresh compose unit (standalone vendors + app) -> run verifier scenarios ->
  verdict JSON under ``artifacts/evals/<eval_id>/``.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import dataclasses
import json
from collections.abc import Coroutine
from pathlib import Path
from typing import Any, TypeVar

from bench.compose import ComposeError, ParticipantResourceError
from bench.config import TaskConfig, VendorMetadata, load_task_config
from bench.eval_output import DEFAULT_OUTPUT_ROOT, EvalDir
from bench.health import wait_for_http
from bench.provenance import capture_provenance
from bench.scoring import (
    SCORER_VERSION,
    ScoringError,
    coverage,
    gold_from_sidecar,
    raw_score,
    solved,
    task_score,
)
from bench.verdict import Verdict
from bench.verifier.checks import CheckRecorder
from bench.verifier.context import AppHandle, VerifierContext, VendorHandle
from bench.verifier.scenario_loader import discover_scenarios, load_scenario_module
from bench.workspace import apply_patch, prepare_pristine_repo

_T = TypeVar("_T")


def failure_class_for_phase(phase: str) -> str:
    """Classify the owner of a grading failure before retry policy sees it."""
    return {
        "candidate_build": "candidate_build_failure",
        "benchmark_startup": "benchmark_infrastructure_failure",
        "compose_render": "benchmark_infrastructure_failure",
        "verifier": "benchmark_verifier_failure",
    }.get(phase, "benchmark_infrastructure_failure")


_BUILD_INFRASTRUCTURE_MARKERS = (
    "auth.docker.io",
    "registry-1.docker.io",
    "failed to fetch anonymous token",
    "no such host",
    "temporary failure in name resolution",
    "i/o timeout",
    "tls handshake timeout",
    "connection reset by peer",
    "cannot connect to the docker daemon",
    "docker daemon fault",
)


def failure_class_for_exception(phase: str, exc: BaseException) -> str:
    """Classify by ownership, including infrastructure faults during builds.

    A Docker build normally belongs to the candidate, but registry/DNS/daemon
    failures do not. Treating those as candidate failures silently converts an
    unavailable host dependency into a score and defeats infrastructure retry.
    """
    if isinstance(exc, ParticipantResourceError):
        return "candidate_runtime_failure"
    detail = str(exc).lower()
    if phase == "candidate_build" and any(
        marker in detail for marker in _BUILD_INFRASTRUCTURE_MARKERS
    ):
        return "benchmark_infrastructure_failure"
    return failure_class_for_phase(phase)


def _run_coro_sync(coro: Coroutine[object, object, _T]) -> _T:
    """Run ``coro`` to completion from sync code.

    ``asyncio.run`` raises when a loop is already running (e.g. PI taskset
    ``finalize`` inside verifiers). In that case drive the coro on a worker
    thread with its own loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


@dataclasses.dataclass
class GradeResult:
    verdict: Verdict
    workspace_dir: Path
    stack: Any | None
    eval_id: str | None = None
    eval_dir: Path | None = None
    raw_score: int | None = None
    task_score: float | None = None
    solved: bool = False
    check_coverage: float | None = None
    missing_checks: list[str] = dataclasses.field(default_factory=list)
    failure_class: str = "candidate_result"


def _vendor_name(task: TaskConfig) -> str:
    if task.vendor and task.vendor in task.vendors:
        return task.vendor
    return next(iter(task.vendors)) if task.vendors else "vendor"


async def _run_scenarios_async(
    task: TaskConfig,
    vendor: VendorMetadata,
    task_dir: Path,
    stack: Any,
    repo_dir: Path,
    service_map: dict[str, str] | None = None,
) -> CheckRecorder:
    output_dir = repo_dir / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    app = AppHandle(stack, task.entry, output_dir=output_dir)
    fixtures = task_dir / "verifier" / "fixtures"
    secrets = dict(vendor.credentials)

    ctx = VerifierContext(
        task=task,
        vendor_metadata=vendor,
        app=app,
        fixtures=fixtures,
        secrets=secrets,
        output_dir=output_dir,
    )
    for name, meta in task.vendors.items():
        service = (service_map or {}).get(name, name)
        ctx._vendors[name] = VendorHandle(stack, meta, service=service)
        ctx.secrets.update(meta.credentials)

    try:
        scenario_paths = discover_scenarios(task_dir, names=task.scenarios)
        for path in scenario_paths:
            module = load_scenario_module(path)
            await module.run(ctx)
    finally:
        pass
    return ctx.recorder


def grade_once(
    task_dir: Path,
    patch_path: Path,
    run_id: str,
    *,
    keep: bool = False,
    workdir_root: Path | None = None,
    startup_timeout_s: float = 120.0,
    output_root: Path | None = None,
    eval_id: str | None = None,
    eval_dir: EvalDir | None = None,
    logical_rollout_id: str | None = None,
) -> GradeResult:
    task_dir = Path(task_dir)
    task = load_task_config(task_dir)
    vendor_name = _vendor_name(task)
    vendor = task.vendors[vendor_name]

    from bench.compose_unit import ComposeUnitStack, task_compose_unit_ready

    ed: EvalDir | None = eval_dir
    if ed is None:
        ed = EvalDir.create(
            output_root=Path(output_root or DEFAULT_OUTPUT_ROOT),
            eval_id=eval_id,
            logical_rollout_id=logical_rollout_id,
        )
    submitted_patch = Path(patch_path).resolve()
    if submitted_patch.is_file() and submitted_patch != ed.patch_path.resolve():
        ed.write_patch_bytes(submitted_patch.read_bytes())
    retained_patch = ed.patch_path if ed.patch_path.is_file() else submitted_patch
    prior_meta = (
        json.loads(ed.meta_path.read_text(encoding="utf-8")) if ed.meta_path.is_file() else {}
    )
    rollout_provenance = prior_meta.get("rollout_provenance") or prior_meta.get("provenance")
    workspace_dir = ed.workspace
    # Fresh pristine repo for grading (wipes prior agent tree if reusing eval_dir).
    repo_dir = prepare_pristine_repo(task_dir, workspace_dir)

    if ed.secret_leak_detected:
        ok, message = False, "runtime secret exfiltration detected and redacted from patch"
    else:
        ok, message = apply_patch(repo_dir, retained_patch)
    if not ok:
        patch_failure_class = (
            "candidate_runtime_failure" if ed.secret_leak_detected else "candidate_patch_failure"
        )
        grading_provenance = capture_provenance(
            task_dir,
            model="standalone-grade",
            provider="none",
            mode="grade",
            resolve_images=False,
        )
        verdict = Verdict.error_verdict(
            task.id, run_id, f"patch did not apply (unresolved, not a crash): {message}"
        )
        if ed is not None:
            ed.write_verdict_dict(verdict.to_dict())
            meta_update = {
                "status": "patch_failed",
                "task": task.id,
                "run_id": run_id,
                "mode": "compose_unit",
                "error": message,
                "provenance": rollout_provenance or grading_provenance,
                "grading_provenance": grading_provenance,
                "failure_class": patch_failure_class,
            }
            if rollout_provenance:
                meta_update["rollout_provenance"] = rollout_provenance
            ed.write_meta(meta_update)
        return GradeResult(
            verdict=verdict,
            workspace_dir=workspace_dir,
            stack=None,
            eval_id=ed.eval_id if ed else None,
            eval_dir=ed.root if ed else None,
            failure_class=patch_failure_class,
        )

    if not task_compose_unit_ready(task_dir):
        raise ComposeError(
            f"{task_dir} references a vendor absent from images.lock.json; "
            "the legacy per-task Compose runtime has been removed"
        )

    grading_provenance = capture_provenance(
        task_dir, model="standalone-grade", provider="none", mode="grade"
    )
    meta_update = {
        "status": "grading",
        "task": task.id,
        "run_id": run_id,
        "mode": "compose_unit",
        "provenance": rollout_provenance or grading_provenance,
        "grading_provenance": grading_provenance,
    }
    if rollout_provenance:
        meta_update["rollout_provenance"] = rollout_provenance
    ed.write_meta(meta_update)

    service_map = {name: name for name in task.vendors}
    stack = ComposeUnitStack(
        task_dir,
        ed,
        app_repo=repo_dir,
        include_agent=False,
        startup_timeout_s=startup_timeout_s,
    )

    phase = "compose_render"
    failure_class = "candidate_result"
    try:
        # MUST re-render. This stack may be a SECOND ComposeUnitStack over an eval
        # dir that eval_once / eval_claude_code_once already rendered, and
        # `_webhook_port = free_port()` runs per instance. Without re-rendering,
        # this stack writes vendor_cfg.json telling the vendor to deliver
        # webhooks to ITS port while the app container is still published on
        # the EVAL stack's port from the stale compose.yaml — so every delivery
        # hits a dead port.
        #
        # Measured 2026-08-08 on task-0002/opus: vendor told
        # host.docker.internal:58020, app published 57689. Result:
        # `tampered=7 accepted=0 responded_non2xx=0` — nothing ever answered,
        # and all five webhook checks failed. The SAME patch graded standalone
        # scored resolved=True with every webhook check passing, because a
        # fresh eval dir has no compose.yaml so the grade stack renders its own
        # and the two ports agree by construction.
        #
        # This silently affected the 14 `serve_start` webhook tasks in the eval
        # lane only, and it UNDERRATED models: opus had fully solved task-0002.
        # Third bug in this family, after the hosted WEBHOOK_TARGET rewrite and
        # serve_stop being a no-op.
        stack.render_compose()
        phase = "candidate_build"
        stack.build(stack.app_service)
        phase = "benchmark_startup"
        stack.up(exclude_app=True)
        for service in service_map.values():
            url = stack.data_base_url_for(service)
            if not url:
                continue
            wait_for_http(f"{url}/_ready", timeout_s=startup_timeout_s)

        phase = "verifier"
        recorder = _run_coro_sync(
            _run_scenarios_async(task, vendor, task_dir, stack, repo_dir, service_map=service_map)
        )
        stack.assert_participant_disk_budget()
        verdict = Verdict(
            task=task.id,
            run_id=run_id,
            l1=recorder.l1,
            hard=recorder.hard,
            soft=recorder.soft,
            l3=recorder.l3,
        )
    except Exception as exc:  # noqa: BLE001
        failure_class = failure_class_for_exception(phase, exc)
        verdict = Verdict.error_verdict(task.id, run_id, str(exc))
    finally:
        vendor_logs = ""
        try:
            vendor_logs = stack.logs("vendors")
        except Exception:
            vendor_logs = ""
        if ed is not None and vendor_logs:
            ed.write_vendor_log(vendor_logs)
        if not keep:
            try:
                stack.down()
            except Exception:
                pass
        stack.cleanup_override_file()

    score_error = None
    score_raw = None
    score_task = None
    score_solved = False
    score_coverage = None
    missing_checks: list[str] = []
    try:
        gold = gold_from_sidecar(task_dir)
        score_raw = raw_score(verdict, gold)
        score_task = task_score(verdict, gold)
        score_solved = solved(verdict, gold)
        _hit, _total, score_coverage, missing_checks = coverage(verdict, gold)
    except ScoringError as exc:
        score_error = str(exc)
        failure_class = "benchmark_scoring_failure"

    if ed is not None:
        ed.write_verdict_dict(verdict.to_dict())
        ed.write_meta(
            {
                "status": "done" if verdict.error is None and score_error is None else "error",
                "resolved": score_solved,
                "legacy_resolved": verdict.resolved,
                "raw_score": score_raw,
                "task_score": score_task,
                "scorer_version": SCORER_VERSION,
                "reward_semantics": "task_score_div_100",
                "check_coverage": score_coverage,
                "missing_checks": missing_checks,
                "failure_class": failure_class,
                "error": verdict.error or score_error,
            }
        )

    return GradeResult(
        verdict=verdict,
        workspace_dir=workspace_dir,
        stack=stack if keep else None,
        eval_id=ed.eval_id if ed else None,
        eval_dir=ed.root if ed else None,
        raw_score=score_raw,
        task_score=score_task,
        solved=score_solved,
        check_coverage=score_coverage,
        missing_checks=missing_checks,
        failure_class=failure_class,
    )

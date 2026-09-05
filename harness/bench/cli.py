"""``bench`` console script — argparse entry point.

Command groups (see ``bench --help``):

- **one task**    ``run``, ``grade``, ``eval``, ``validate``
- **whole suite** ``run-all``, ``grade-all``, ``validate-suite``, ``scoring-status``
- **environment** ``setup``, ``build-agents``, ``build-vendors``, ``pull``
- **release**     ``release-check``

Exit-code contract, uniform across every subcommand:

- ``0`` — the command did what it was asked.
- ``1`` — the run completed but the *result* is negative: a verdict carries an
  error, a task did not resolve, or an ``--enforce`` gate failed. The number you
  got is real; it just is not a pass.
- ``2`` — the command could not run: bad usage, missing task/patch, missing API
  key, absent Docker image, broken task config. **No result was produced**, so
  anything downstream must not treat this as a score.

Keeping 1 and 2 distinct is the point. A sweep driver that cannot tell "the model
scored zero" from "the harness never started" will happily publish the second as
the first.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from bench import __version__

from bench.commands.eval_core import (
    eval_claude_code_once,
    eval_codex_once,
    eval_once,
    eval_opencode_once,
)
from bench.commands.grade_all import default_patch_resolver, grade_all
from bench.commands.scoring_status import render as render_scoring_status
from bench.commands.scoring_status import scoring_status
from bench.commands.grading_core import grade_once
from bench.commands.rollout_core import run_once
from bench.commands.run_all import run_all
from bench.commands.validate import validate_task
from bench.commands.validate_suite import lint_suite, write_json_report
from bench.compose import ComposeError
from bench.compose_unit import build_agent_images
from bench.config import ConfigError
from bench.eval_output import DEFAULT_OUTPUT_ROOT, new_eval_id
from bench.images import ImageError, build_vendor_images, pull_vendor_images
from bench.providers import ProviderError


# Turn budget. The real budget is wall clock (per-task `timeout_minutes`, 60-90
# min across the suite, enforced by every eval path); max_turns is a backstop
# against a runaway tool loop, so it must sit well ABOVE any legitimate rollout
# rather than acting as a second, invisible budget.
#
# Measured, not guessed: task-0001 — among the lightest tasks in the suite, 129s
# to grade — needed 52 turns to solve. A prior sonnet sweep hit a 100-turn cap on
# 19 of 50 tasks, so >100 turns is normal for the middle of the distribution and
# the old default of 40 truncated even easy tasks. 300 leaves the wall clock as
# the binding constraint, which is the one we can reason about in cost terms.
_DEFAULT_MAX_TURNS = 300

_EXIT_CODES = """\
exit codes:
  0  did what was asked
  1  ran, but the result is negative (verdict error, task unresolved,
     --enforce gate failed) — a real measurement that is not a pass
  2  could not run (bad usage, missing task/patch/API key/image, bad config)
     — NO result was produced; downstream must not read this as a score
"""

_MAIN_EPILOG = (
    _EXIT_CODES
    + """
common workflows:
  # grade the reference solution and the do-nothing floor for one task
  bench grade --task ../tasks/public/task-0001 --patch ../tasks/public/task-0001/authoring/solution.patch
  bench grade --task ../tasks/public/task-0001 --patch /dev/null

  # run a model on one task (needs the provider key in .env), then a whole suite
  bench eval --harness direct --task ../tasks/public/task-0001 --model sonnet
  bench eval --harness claude-code --task ../tasks/public/task-0001
  bench eval --harness codex --task ../tasks/public/task-0001 --model gpt-5.6-terra
  bench eval --harness opencode --task ../tasks/public/task-0001 --model moonshotai/kimi-k2.5

  # authoring gates
  bench validate --task ../tasks/public/task-0001 --strict
  bench scoring-status --tasks-dir ../tasks --enforce

environment:
  IB_IMAGE_MODE   local (standalone <vendor>:local images) or locked
                  (immutable images.lock.json digests). Scored/CI defaults locked.
  IB_VENDOR_IMAGE_<ID>  per-vendor override; must be digest-pinned in locked mode
  IB_CLAUDE_WORK  Claude Code config dir for --harness claude-code (~/.claude-work)
  IB_CODEX_HOME   Codex CODEX_HOME for --harness codex (~/.codex). Its auth.json is
                  bind-mounted into the agent; each dir is a separate plan, so
                  this is the account-rotation knob for a codex sweep.
"""
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bench",
        description=(
            "Integration-Bench harness: roll out agents against integration tasks, "
            "grade patches, and run the authoring gates."
        ),
        epilog=_MAIN_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(
        dest="command",
        required=True,
        metavar="<command>",
        title="commands",
    )

    p_setup = sub.add_parser(
        "setup",
        help="Prepare local configuration and standalone runtime images",
        description=(
            "Check Docker, build selected standalone vendor images and all pinned "
            "agent-harness images, and create .env from .env.example when needed. "
            "The repository ./bench launcher bootstraps .venv before dispatch."
        ),
        epilog=_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_setup.add_argument("--vendor", action="append", help="Vendor ID (repeatable; default: all)")
    p_setup.add_argument(
        "--skip-images",
        action="store_true",
        help="Prepare the CLI/config only; do not contact Docker",
    )
    p_setup.add_argument(
        "--skip-agents",
        action="store_true",
        help="Build vendor images but skip agent-harness images",
    )

    p_run = sub.add_parser(
        "run",
        help="Roll out an agent against one task (you supply the agent command)",
        description=(
            "Start one task's vendors, run an arbitrary agent command against the "
            "workspace, and save the resulting patch. Does NOT grade — pipe the "
            "patch into `bench grade`. For a provider-driven loop use `bench eval`."
        ),
        epilog=_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_run.add_argument("--task", required=True, metavar="DIR", help="Path to the task directory")
    p_run.add_argument(
        "--agent", required=True, metavar="CMD", help="Shell command that edits /workspace"
    )
    p_run.add_argument(
        "--run-id", default=None, metavar="ID", help="Run identifier (default: run-<task>-<rand>)"
    )
    p_run.add_argument(
        "--predictions-dir",
        default="predictions",
        metavar="DIR",
        help="Where to write <task>/<run-id>.patch (default: %(default)s)",
    )
    p_run.add_argument(
        "--workdir-root",
        default=None,
        metavar="DIR",
        help="Retained rollout artifact root (default: artifacts/runs)",
    )
    p_run.add_argument("--keep", action="store_true", help="Skip compose teardown for debugging")

    p_grade = sub.add_parser(
        "grade",
        help="Grade a patch against one task",
        description=(
            "Apply a patch to a pristine copy of the task repo, run every verifier "
            "scenario, and write a verdict. Pass an empty patch (/dev/null) to "
            "measure the do-nothing floor."
        ),
        epilog=_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_grade.add_argument("--task", required=True, metavar="DIR", help="Path to the task directory")
    p_grade.add_argument(
        "--patch",
        required=True,
        metavar="FILE",
        help="Unified diff to apply; an empty file grades the starter repo as-is",
    )
    p_grade.add_argument("--run-id", default=None, metavar="ID", help="Run identifier")
    p_grade.add_argument(
        "--eval-id", default=None, metavar="HEX32", help="32-hex eval id (default: random)"
    )
    p_grade.add_argument(
        "--logical-rollout-id",
        default=None,
        help="Stable model/task/seed rollout id shared by immutable retry attempts",
    )
    p_grade.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        metavar="DIR",
        help="Root for per-eval artifacts (default: %(default)s)",
    )
    p_grade.add_argument(
        "--out", default=None, metavar="FILE", help="Also write a verdict JSON copy here"
    )
    p_grade.add_argument("--keep", action="store_true", help="Skip compose teardown for debugging")

    p_validate = sub.add_parser(
        "validate",
        help="Run the authoring gauntlet for one task",
        description=(
            "Grade the task N times to check it is deterministic, then measure the "
            "do-nothing floor. This is the authoring gate: it answers whether the "
            "task is stable and whether the starter repo already passes it."
        ),
        epilog=_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_validate.add_argument(
        "--task", required=True, metavar="DIR", help="Path to the task directory"
    )
    p_validate.add_argument(
        "--runs",
        type=int,
        default=5,
        metavar="N",
        help="Gold grades to run for determinism (default: %(default)s)",
    )
    p_validate.add_argument(
        "--keep", action="store_true", help="Skip compose teardown for debugging"
    )
    p_validate.add_argument(
        "--write-baseline",
        action="store_true",
        help=(
            "On a passing gauntlet, write/refresh the do-nothing floor sidecar "
            "at <task>/verifier/empty-baseline.json. Without this flag, an "
            "existing sidecar is instead checked for drift (recomputed probe "
            "fractions must match the stored ones)."
        ),
    )
    p_validate.add_argument(
        "--strict",
        action="store_true",
        help=(
            "Also enforce the hardening bar: floor <= 0.40*gold and "
            "gold-floor >= 0.25. Required for reworked tasks; off by default "
            "so baseline re-measurement of legacy tasks is not blocked."
        ),
    )

    p_validate_suite = sub.add_parser(
        "validate-suite",
        help="Run the suite-level uniqueness lint over tasks/ (no Docker)",
        description=(
            "Static, Docker-free lint across a whole tasks/ tree: duplicate "
            "mechanics, vendor reuse, ticket leaks. Reports by default; use "
            "--enforce to turn FAIL findings into exit 1."
        ),
        epilog=_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_validate_suite.add_argument(
        "--tasks-dir",
        default="tasks/public",
        metavar="DIR",
        help="Tree of task-* dirs, relative to CWD (default: %(default)s)",
    )
    p_validate_suite.add_argument(
        "--enforce",
        action="store_true",
        help="Exit 1 if any FAIL-level finding exists (default: report only, exit 0)",
    )
    p_validate_suite.add_argument(
        "--json", default=None, help="Also write the full report as JSON to this path"
    )
    p_validate_suite.add_argument(
        "--compare-tasks-dir",
        default=None,
        help=(
            "A second tasks/ tree (e.g. the held-out suite) to cross-check against: "
            "WARNs on same-vendor + same-mechanic-family reuse across the two suites"
        ),
    )
    p_validate_suite.add_argument(
        "--compare-label",
        default=None,
        help="Name to report the --compare-tasks-dir suite under (default: its parent dir name)",
    )

    p_run_all = sub.add_parser(
        "run-all",
        help="Roll out an agent against every task under tasks/",
        description=(
            "Sequential rollout over a whole tasks/ tree, one JSONL row per task. "
            "Does not grade; feed the patches to grade-all."
        ),
        epilog=_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_run_all.add_argument(
        "--tasks-dir",
        default="tasks/public",
        metavar="DIR",
        help="Tree of task-* dirs (default: %(default)s)",
    )
    p_run_all.add_argument(
        "--agent", required=True, metavar="CMD", help="Shell command that edits /workspace"
    )
    p_run_all.add_argument(
        "--results",
        default="results.jsonl",
        metavar="FILE",
        help="JSONL output (default: %(default)s)",
    )
    p_run_all.add_argument(
        "--predictions-dir",
        default="predictions",
        metavar="DIR",
        help="Where patches are written (default: %(default)s)",
    )
    p_run_all.add_argument(
        "--workdir-root", default=None, metavar="DIR", help="Rollout workspace scratch root"
    )
    p_run_all.add_argument(
        "--keep", action="store_true", help="Skip compose teardown for debugging"
    )

    p_grade_all = sub.add_parser(
        "grade-all",
        help="Grade every task under tasks/",
        description=(
            "Grade a whole tasks/ tree, one JSONL row per task. Looks up "
            "predictions/<task>/<run-id>.patch and falls back to the task's own "
            "solution.patch, so with no predictions this measures the gold ceiling."
        ),
        epilog=_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_grade_all.add_argument(
        "--tasks-dir",
        default="tasks/public",
        metavar="DIR",
        help="Tree of task-* dirs (default: %(default)s)",
    )
    p_grade_all.add_argument(
        "--predictions-dir",
        default="predictions",
        help="Root dir to look up predictions/<task>/<run-id>.patch; falls back to solution.patch",
    )
    p_grade_all.add_argument("--results", default="results.jsonl")
    p_grade_all.add_argument("--keep", action="store_true")

    p_pull = sub.add_parser(
        "pull",
        help="Pull locked standalone vendor images",
        description=(
            "Pull immutable vendor image digests from images.lock.json. "
            "Missing promotion metadata is a hard failure."
        ),
        epilog=_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_pull.add_argument("--vendor", action="append", help="Vendor ID (repeatable)")

    p_build_vendors = sub.add_parser(
        "build-vendors",
        help="Build standalone vendor images for local development",
        epilog=_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_build_vendors.add_argument("--vendor", action="append", help="Vendor ID (repeatable)")
    p_build_vendors.add_argument("--platform", default=None)

    p_build_agents = sub.add_parser(
        "build-agents",
        help="Build pinned local images for evaluation harnesses",
        epilog=_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_build_agents.add_argument(
        "--harness",
        action="append",
        choices=("direct", "claude-code", "codex", "opencode"),
        help="Harness image to build (repeatable; default: all)",
    )
    p_build_agents.add_argument(
        "--force", action="store_true", help="Rebuild even when the Dockerfile hash matches"
    )

    p_release_check = sub.add_parser(
        "release-check",
        help="Run repository and release-readiness gates",
        description=(
            "Validate schemas, boundaries, generated files, and release metadata. "
            "Use --require-promoted-images for scored/published release readiness."
        ),
        epilog=_EXIT_CODES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_release_check.add_argument(
        "--require-promoted-images",
        action="store_true",
        help="Require immutable registry digests, SBOMs, and signatures for every vendor",
    )

    p_eval = sub.add_parser(
        "eval",
        help="Run one task with a selected agent harness, then grade",
        description=(
            "Drive one task through the direct API loop, Claude Code, Codex CLI, "
            "or OpenCode, then grade the diff it produced. Credentials are "
            "checked before the rollout starts; missing auth exits 2 and does not "
            "produce a score."
        ),
        epilog=_EXIT_CODES
        + """
examples:
  bench eval --harness direct --task tasks/public/task-0001 --model sonnet
  bench eval --harness claude-code --task tasks/public/task-0001
  bench eval --harness codex --task tasks/public/task-0001 --model gpt-5.6-terra
  bench eval --harness opencode --task tasks/public/task-0001 --model moonshotai/kimi-k2.5 --variant high

turn budget:
  --max-turns applies only to --harness direct. It is a HARD cap, not a timeout.
  A rollout that hits it is truncated mid-task and grades as a failure, so a low
  cap measures the cap rather than the model. Task-0001, one of the lightest
  tasks, has been observed needing 52 turns.
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p_eval.add_argument(
        "--harness",
        choices=("direct", "claude-code", "codex", "opencode"),
        default="direct",
        help="Agent harness to execute (default: %(default)s)",
    )
    p_eval.add_argument("--task", required=True, metavar="DIR", help="Path to the task directory")
    p_eval.add_argument(
        "--model",
        default=None,
        metavar="MODEL",
        help=(
            "Model id or alias. Required for direct and opencode; optional for "
            "claude-code and codex, which otherwise use their CLI default"
        ),
    )
    p_eval.add_argument(
        "--variant",
        default=None,
        help="OpenCode model variant/reasoning effort (only with --harness opencode)",
    )
    p_eval.add_argument(
        "--max-turns",
        type=int,
        default=None,
        metavar="N",
        help=f"Direct-loop turn cap (default: {_DEFAULT_MAX_TURNS}); see notes below",
    )
    p_eval.add_argument(
        "--eval-id", default=None, metavar="HEX32", help="32-hex eval id (default: random)"
    )
    p_eval.add_argument(
        "--logical-rollout-id",
        default=None,
        help="Stable model/task/seed rollout id shared by immutable retry attempts",
    )
    p_eval.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        metavar="DIR",
        help="Root for per-eval artifacts (default: %(default)s)",
    )
    p_eval.add_argument("--keep", action="store_true", help="Skip compose teardown for debugging")

    p_score = sub.add_parser(
        "scoring-status",
        help="Report per-test scoring migration status across tasks/ (no Docker)",
    )
    p_score.add_argument("--tasks-dir", default="tasks/public")
    p_score.add_argument("-v", "--verbose", action="store_true")
    p_score.add_argument(
        "--enforce",
        action="store_true",
        help="exit 1 unless every task passes validate_scoring",
    )

    return parser


def _require_task_dir(value: str) -> Path:
    """Validate a --task argument, raising the exit-2 family on failure.

    Deliberately NOT an argparse ``type=``: paths are checked when the command
    runs, not when it parses, so ``--help`` and the parser tests stay free of
    filesystem dependencies.
    """
    p = Path(value)
    if not p.exists():
        raise FileNotFoundError(f"task directory does not exist: {p}")
    if not p.is_dir():
        raise FileNotFoundError(f"--task must be a directory, got a file: {p}")
    if not (p / "task.yaml").is_file():
        raise FileNotFoundError(
            f"{p} has no task.yaml, so it is not a task directory. "
            "Point --task at e.g. ../tasks/public/task-0001."
        )
    return p


def _require_patch_file(value: str) -> Path:
    p = Path(value)
    if not p.exists():
        raise FileNotFoundError(
            f"patch file does not exist: {p} (use /dev/null to grade the starter repo unmodified)"
        )
    if p.is_dir():
        raise FileNotFoundError(f"--patch must be a file, got a directory: {p}")
    return p


def _require_tasks_dir(value: str) -> Path:
    p = Path(value)
    if not p.is_dir():
        raise FileNotFoundError(f"tasks directory does not exist: {p}")
    if not any(p.glob("task-*")):
        raise FileNotFoundError(
            f"no task-* directories under {p}. Pass --tasks-dir explicitly; it is "
            "resolved relative to the current working directory."
        )
    return p


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _cmd_setup(args: argparse.Namespace) -> int:
    repo_root = _repo_root()
    env_file = repo_root / ".env"
    env_example = repo_root / ".env.example"
    if not env_file.exists():
        if not env_example.is_file():
            raise FileNotFoundError(f"missing setup template: {env_example}")
        shutil.copyfile(env_example, env_file)

    vendor_refs: dict[str, str] = {}
    agent_refs: dict[str, str] = {}
    if not args.skip_images:
        docker = subprocess.run(
            ["docker", "info"], capture_output=True, text=True, errors="replace"
        )
        if docker.returncode != 0:
            raise ComposeError("Docker is installed but its daemon is not reachable")
        compose = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True,
            text=True,
            errors="replace",
        )
        if compose.returncode != 0:
            raise ComposeError("Docker Compose v2 (`docker compose`) is required")
        vendor_refs = build_vendor_images(args.vendor)
        if not args.skip_agents:
            agent_refs = build_agent_images()
    print(
        json.dumps(
            {
                "repo": str(repo_root),
                "env": str(env_file),
                "vendor_images": vendor_refs,
                "agent_images": agent_refs,
                "images_skipped": args.skip_images,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _cmd_run(args: argparse.Namespace) -> int:
    task_dir = _require_task_dir(args.task)
    run_id = args.run_id or f"run-{task_dir.name}-{uuid.uuid4().hex[:8]}"
    result = run_once(
        task_dir,
        args.agent,
        run_id,
        keep=args.keep,
        workdir_root=Path(args.workdir_root) if args.workdir_root else None,
        predictions_dir=Path(args.predictions_dir),
    )
    print(
        json.dumps(
            {
                "task": result.task_id,
                "run_id": result.run_id,
                "eval_id": result.eval_id,
                "patch_path": str(result.patch_path) if result.patch_path else None,
                "run_manifest_path": str(result.run_manifest_path),
                "timed_out": result.timed_out,
                "agent_returncode": result.agent_returncode,
            },
            indent=2,
        )
    )
    return 0


def _cmd_grade(args: argparse.Namespace) -> int:
    task_dir = _require_task_dir(args.task)
    patch_path = _require_patch_file(args.patch)
    run_id = args.run_id or new_eval_id()
    result = grade_once(
        task_dir,
        patch_path,
        run_id,
        keep=args.keep,
        output_root=Path(args.output_root) if args.output_root else DEFAULT_OUTPUT_ROOT,
        eval_id=args.eval_id,
        logical_rollout_id=args.logical_rollout_id,
    )
    if args.out:
        result.verdict.write(Path(args.out))
    summary = {
        "task": result.verdict.task,
        "run_id": result.verdict.run_id,
        "eval_id": result.eval_id,
        "output_dir": str(result.eval_dir) if result.eval_dir else None,
        "run_manifest_path": (
            str(Path(result.eval_dir) / "run-manifest.json") if result.eval_dir else None
        ),
        "resolved": result.solved,
        "legacy_resolved": result.verdict.resolved,
        "raw_score": result.raw_score,
        "task_score": result.task_score,
        "check_coverage": result.check_coverage,
        "missing_checks": result.missing_checks,
        "failure_class": result.failure_class,
        "error": result.verdict.error,
    }
    print(json.dumps(summary, indent=2))
    print(result.verdict.to_json())
    return 0 if result.verdict.error is None else 1


def _print_eval_result(result: object, *, harness: str) -> int:
    print(
        json.dumps(
            {
                "harness": harness,
                "eval_id": result.eval_id,
                "task": result.task_id,
                "model": result.model,
                "provider": result.provider,
                "turns": result.turns,
                "timed_out": result.timed_out,
                "stop_reason": result.stop_reason,
                "truncated": result.stop_reason in ("turn_cap", "wall_clock"),
                "resolved": result.resolved,
                "reward": result.reward,
                "raw_score": result.raw_score,
                "task_score": result.task_score,
                "check_coverage": result.check_coverage,
                "missing_checks": result.missing_checks,
                "failure_class": result.failure_class,
                "output_dir": str(result.output_dir),
                "run_manifest_path": str(result.output_dir / "run-manifest.json"),
                "patch_path": str(result.patch_path) if result.patch_path else None,
                "verdict_path": str(result.verdict_path) if result.verdict_path else None,
                "error": result.error,
            },
            indent=2,
        )
    )
    if result.error and not result.resolved:
        return 1
    return 0


def _cmd_eval(args: argparse.Namespace) -> int:
    harness = args.harness
    model = args.model
    variant = args.variant
    max_turns = args.max_turns

    if harness in ("direct", "opencode") and not model:
        raise ValueError(f"--model is required when --harness {harness}")
    if variant and harness != "opencode":
        raise ValueError("--variant is only valid with --harness opencode")
    if max_turns is not None and harness != "direct":
        raise ValueError("--max-turns is only valid with --harness direct")

    task_dir = _require_task_dir(args.task)
    common = {
        "keep": args.keep,
        "output_root": Path(args.output_root) if args.output_root else DEFAULT_OUTPUT_ROOT,
        "eval_id": args.eval_id,
        "logical_rollout_id": args.logical_rollout_id,
    }
    if harness == "direct":
        result = eval_once(
            task_dir,
            model,
            max_turns=max_turns if max_turns is not None else _DEFAULT_MAX_TURNS,
            **common,
        )
    elif harness == "claude-code":
        result = eval_claude_code_once(task_dir, model=model, **common)
    elif harness == "codex":
        result = eval_codex_once(task_dir, model=model, **common)
    elif harness == "opencode":
        result = eval_opencode_once(task_dir, model=model, variant=variant, **common)
    else:  # argparse enforces this for the public command.
        raise ValueError(f"unknown eval harness: {harness}")
    return _print_eval_result(result, harness=harness)


def _cmd_validate(args: argparse.Namespace) -> int:
    report = validate_task(
        Path(args.task),
        runs=args.runs,
        keep=args.keep,
        write_baseline=args.write_baseline,
        strict=args.strict,
    )
    print(report.render())
    return 0 if report.passed else 1


def _cmd_scoring_status(args: argparse.Namespace) -> int:
    rows = scoring_status(Path(args.tasks_dir))
    print(render_scoring_status(rows, verbose=args.verbose))
    if args.enforce and any(not r.ok for r in rows):
        return 1
    return 0


def _cmd_validate_suite(args: argparse.Namespace) -> int:
    report = lint_suite(
        Path(args.tasks_dir),
        compare_tasks_dir=Path(args.compare_tasks_dir) if args.compare_tasks_dir else None,
        compare_label=args.compare_label,
    )
    print(report.render())
    if args.json:
        write_json_report(report, Path(args.json))
    if args.enforce and report.fail_count > 0:
        return 1
    return 0


def _cmd_run_all(args: argparse.Namespace) -> int:
    results = run_all(
        Path(args.tasks_dir),
        args.agent,
        results_path=Path(args.results),
        keep=args.keep,
        workdir_root=Path(args.workdir_root) if args.workdir_root else None,
        predictions_dir=Path(args.predictions_dir),
    )
    print(f"ran {len(results)} task(s); results -> {args.results}")
    return 0


def _cmd_grade_all(args: argparse.Namespace) -> int:
    resolver = default_patch_resolver(Path(args.predictions_dir))
    results = grade_all(
        Path(args.tasks_dir),
        resolver,
        results_path=Path(args.results),
        keep=args.keep,
    )
    resolved_count = sum(1 for r in results if r.get("resolved"))
    print(f"graded {len(results)} task(s), {resolved_count} resolved; results -> {args.results}")
    return 0


def _cmd_pull(args: argparse.Namespace) -> int:
    refs = pull_vendor_images(args.vendor)
    print(json.dumps({"images": refs}, indent=2, sort_keys=True))
    return 0


def _cmd_build_vendors(args: argparse.Namespace) -> int:
    refs = build_vendor_images(args.vendor, platform=args.platform)
    print(json.dumps({"images": refs}, indent=2, sort_keys=True))
    return 0


def _cmd_build_agents(args: argparse.Namespace) -> int:
    refs = build_agent_images(args.harness, force=args.force)
    print(json.dumps({"images": refs}, indent=2, sort_keys=True))
    return 0


def _cmd_release_check(args: argparse.Namespace) -> int:
    command = [
        sys.executable,
        str(_repo_root() / "tools" / "validation" / "validate_repo.py"),
    ]
    if args.require_promoted_images:
        command.append("--require-promoted-images")
    result = subprocess.run(
        command,
        cwd=_repo_root(),
        capture_output=True,
        text=True,
        errors="replace",
    )
    output = (result.stdout or result.stderr or "").strip()
    if output:
        print(output)
    return 0 if result.returncode == 0 else 1


_HANDLERS = {
    "setup": _cmd_setup,
    "run": _cmd_run,
    "grade": _cmd_grade,
    "validate": _cmd_validate,
    "validate-suite": _cmd_validate_suite,
    "scoring-status": _cmd_scoring_status,
    "run-all": _cmd_run_all,
    "grade-all": _cmd_grade_all,
    "pull": _cmd_pull,
    "build-vendors": _cmd_build_vendors,
    "build-agents": _cmd_build_agents,
    "release-check": _cmd_release_check,
    "eval": _cmd_eval,
}


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return _HANDLERS[args.command](args)
    except (
        ConfigError,
        FileNotFoundError,
        ImageError,
        ProviderError,
        ComposeError,
        ValueError,
    ) as exc:
        print(f"bench: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

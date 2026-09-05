#!/usr/bin/env python3
"""Install the harness and prepare the locked standalone vendor images.

Usage (from this repo root):

    ./bench setup

Requires: Python 3.10+, Docker on PATH.
Does not require Prime Intellect.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HARNESS = ROOT / "harness"
ENV_EXAMPLE = ROOT / ".env.example"
ENV_FILE = ROOT / ".env"
VENV = ROOT / ".venv"
VENV_PYTHON = VENV / "bin" / "python"


def _die(msg: str, code: int = 1) -> None:
    print(f"setup: {msg}", file=sys.stderr)
    raise SystemExit(code)


def _ensure_docker() -> None:
    if shutil.which("docker") is None:
        _die("docker not found on PATH — install Docker Desktop / Engine first")
    proc = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if proc.returncode != 0:
        _die("docker is installed but not reachable (is the daemon running?)")
    proc = subprocess.run(
        ["docker", "compose", "version"], capture_output=True, text=True
    )
    if proc.returncode != 0:
        _die(
            "Docker Compose v2 is required (`docker compose`); install the "
            "Compose plugin before running the benchmark"
        )


def _install_harness() -> None:
    if not (HARNESS / "pyproject.toml").is_file():
        _die(f"missing harness at {HARNESS}")
    if not VENV_PYTHON.is_file():
        print(f"setup: creating isolated environment → {VENV}")
        proc = subprocess.run([sys.executable, "-m", "venv", str(VENV)], cwd=ROOT)
        if proc.returncode != 0:
            _die("python -m venv .venv failed", proc.returncode)
    print("setup: installing harness (editable) + eval extras into .venv …")
    proc = subprocess.run(
        [
            str(VENV_PYTHON),
            "-m",
            "pip",
            "install",
            "-e",
            f"{HARNESS}[eval]",
        ],
        cwd=ROOT,
    )
    if proc.returncode != 0:
        _die("pip install -e harness[eval] failed", proc.returncode)


def _prepare_vendor_images(vendors: list[str] | None = None) -> str:
    has_vendor_source = (ROOT / "vendors").is_dir()
    action = "building" if has_vendor_source else "pulling"
    helper = "build_vendor_images" if has_vendor_source else "pull_vendor_images"
    print(f"setup: {action} standalone vendor images …")
    code = (
        "from pathlib import Path; "
        f"from bench.images import {helper}; "
        f"print({helper}({vendors!r}, repo_root=Path({str(ROOT)!r})))"
    )
    proc = subprocess.run(
        [str(VENV_PYTHON), "-c", code], cwd=ROOT, capture_output=True, text=True
    )
    if proc.returncode != 0:
        _die(
            f"unable to prepare the standalone vendor images by {action}. "
            "Check Docker and the image lock/build output.\n"
            + (proc.stderr or proc.stdout or "").strip(),
            proc.returncode,
        )
    refs = proc.stdout.strip().splitlines()[-1]
    print("setup: vendor images ready")
    return refs


def _build_agent_images() -> str:
    print("setup: ensuring direct, Claude Code, Codex, and OpenCode agent images …")
    code = (
        "from bench.compose_unit import build_agent_images; print(build_agent_images())"
    )
    proc = subprocess.run(
        [str(VENV_PYTHON), "-c", code], cwd=ROOT, capture_output=True, text=True
    )
    if proc.returncode != 0:
        _die(
            (proc.stderr or proc.stdout or "agent image build failed").strip(),
            proc.returncode,
        )
    tag = proc.stdout.strip().splitlines()[-1]
    print(f"setup: agent images ready → {tag}")
    return tag


def _ensure_env() -> None:
    if ENV_FILE.is_file():
        print(f"setup: leaving existing {ENV_FILE.name}")
        return
    if not ENV_EXAMPLE.is_file():
        _die(f"missing {ENV_EXAMPLE}")
    ENV_FILE.write_text(ENV_EXAMPLE.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"setup: wrote {ENV_FILE.name} from .env.example — fill provider API keys")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-images",
        action="store_true",
        help="install the local CLI without building Docker images (CI/tests)",
    )
    parser.add_argument(
        "--vendor",
        action="append",
        help="vendor ID to build/pull (repeatable; default: all)",
    )
    parser.add_argument(
        "--skip-agents",
        action="store_true",
        help="prepare vendor images but skip agent-harness images",
    )
    args = parser.parse_args()
    print(f"setup: repo root {ROOT}")
    _install_harness()
    if args.skip_images:
        print("setup: skipping Docker image build by request")
    else:
        _ensure_docker()
        _prepare_vendor_images(args.vendor)
        if not args.skip_agents:
            _build_agent_images()
    _ensure_env()
    gold_patch = (
        ROOT / "tasks" / "public" / "task-0001" / "authoring" / "solution.patch"
    )
    if gold_patch.is_file():
        local_check = """  2. Grade gold (no API key):
       ./bench grade --task tasks/public/task-0001 --patch tasks/public/task-0001/authoring/solution.patch"""
    else:
        local_check = """  2. Run the public repository checks (no API key):
       ./bench scoring-status --tasks-dir tasks/public --enforce
       ./bench validate-suite --tasks-dir tasks/public --enforce"""
    print(
        f"""
setup: done.

Next:
  1. Edit .env — set at least one of ANTHROPIC_API_KEY / OPENAI_API_KEY /
     DEEPSEEK_API_KEY / OPENROUTER_API_KEY (needed for `bench eval` only).
{local_check}
  3. Agent smoke (needs a key):
       set -a && source .env && set +a
       ./bench eval --harness direct --task tasks/public/task-0001 --model sonnet --max-turns 30

Artifacts: artifacts/evals/<eval_id>/
See docs/user-guide/quickstart.md.
"""
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

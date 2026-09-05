"""Stub-patch generator for the gauntlet's do-nothing floor probe (gate 2b,
see bench.commands.validate and docs/conduct-rules.md gate 2).

A "stub" submission is a two-line clean-exit entry point: the task's package
runs, exits 0, and does nothing else. Unlike the empty patch (which never even
starts the app), a stub submission lets scenarios run to completion — which
means it can bank any verifier check that passes vacuously on an empty request
log (e.g. a prohibition like "no credentials in query string"). Grading this
patch alongside the empty patch is what lets `bench validate` measure the real
do-nothing floor instead of assuming it is zero.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

import yaml

STUB_MAIN_BODY = "import sys\nsys.exit(0)\n"
STUB_JS_BODY = "process.exit(0);\n"
STUB_SH_BODY = "#!/bin/sh\nexit 0\n"


def _entry_package(task_dir: Path) -> str | None:
    """Return the `<pkg>` in a `["python", "-m", "<pkg>", ...]` entry command,
    or None if task.yaml is missing/malformed or the entry doesn't match that
    shape."""
    task_yaml = task_dir / "task.yaml"
    if not task_yaml.is_file():
        return None
    try:
        spec = yaml.safe_load(task_yaml.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return None
    if not isinstance(spec, dict):
        return None

    command = (spec.get("entry") or {}).get("command", [])
    if isinstance(command, str):
        command = command.split()
    if not isinstance(command, list):
        return None

    if len(command) < 3 or command[0] != "python" or command[1] != "-m":
        return None
    pkg = command[2]
    if not isinstance(pkg, str) or not pkg:
        return None
    return pkg


def _find_main(task_dir: Path, pkg: str) -> Path | None:
    """Locate `<pkg>`'s `__main__.py` under task_dir/repo. None if absent."""
    repo_dir = task_dir / "repo"
    if not repo_dir.is_dir():
        return None
    for path in repo_dir.rglob("__main__.py"):
        if pkg in path.parts:
            return path
    return None


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def stub_patch_text(task_dir: Path) -> str | None:
    """Build a git patch that overwrites the task's `__main__.py` entry point
    with a two-line clean-exit stub (`sys.exit(0)`), so the rest of the
    scenario can run to completion without the submission doing any work.

    Returns None when:
      - task.yaml's entry command doesn't match `["python", "-m", "<pkg>", ...]`
      - no `__main__.py` for `<pkg>` exists under task_dir/repo
      - the resulting diff is empty (the stub is identical to what's already there)
    """
    task_dir = Path(task_dir)
    task_yaml = task_dir / "task.yaml"
    try:
        spec = yaml.safe_load(task_yaml.read_text(encoding="utf-8")) or {}
        command = (spec.get("entry") or {}).get("command", [])
    except (OSError, yaml.YAMLError, AttributeError):
        return None
    if isinstance(command, str):
        command = command.split()
    if not isinstance(command, list) or not command:
        return None

    repo_src = task_dir / "repo"
    if not repo_src.is_dir():
        return None
    main_path: Path | None = None
    body = STUB_MAIN_BODY
    pkg = _entry_package(task_dir)
    if pkg is not None:
        main_path = _find_main(task_dir, pkg)
    elif len(command) >= 2 and command[0] in {"python", "python3"} and command[1] != "-m":
        candidate = repo_src / str(command[1])
        if candidate.resolve().is_relative_to(repo_src.resolve()):
            main_path = candidate
    elif len(command) >= 2 and command[0] == "node":
        candidate = repo_src / str(command[1])
        if candidate.resolve().is_relative_to(repo_src.resolve()):
            main_path, body = candidate, STUB_JS_BODY
    elif len(command) == 1 and str(command[0]).startswith("./"):
        candidate = repo_src / str(command[0])[2:]
        if candidate.is_file() and candidate.resolve().is_relative_to(repo_src.resolve()):
            main_path, body = candidate, STUB_SH_BODY
    if main_path is None:
        return None

    main_rel = main_path.relative_to(repo_src)

    with tempfile.TemporaryDirectory(prefix="bench-stub-") as tmp:
        repo_copy = Path(tmp) / "repo"
        shutil.copytree(repo_src, repo_copy)

        # Throwaway baseline commit; identity doesn't matter beyond existing.
        _git("init", "-q", cwd=repo_copy)
        _git("config", "user.name", "a", cwd=repo_copy)
        _git("config", "user.email", "a@b", cwd=repo_copy)
        _git("add", "-A", cwd=repo_copy)
        _git("commit", "-q", "--allow-empty", "-m", "bench-stub: baseline", cwd=repo_copy)

        target = repo_copy / main_rel
        mode = target.stat().st_mode if target.exists() else 0o100644
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body, encoding="utf-8")
        target.chmod(mode)

        _git("add", "-A", cwd=repo_copy)
        diff = _git("diff", "--cached", "--binary", "HEAD", cwd=repo_copy)
        text = diff.stdout
        if not text.strip():
            return None
        return text

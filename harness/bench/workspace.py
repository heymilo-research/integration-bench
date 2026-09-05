"""Participant workspace projection, Git baselines, and patch handling."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path, PurePosixPath

import yaml

BASELINE_TAG = "bench-baseline"


class WorkspaceError(RuntimeError):
    pass


_RESERVED_TASK_ROOTS = {
    "authoring",
    "compose.override.yaml",
    "docker-compose.yaml",
    "mutations.yaml",
    "solution.patch",
    "task.yaml",
    "variants",
    "vendor.yaml",
    "verifier",
}


def _declared_workspace_entries(task_dir: Path) -> list[str]:
    task_path = task_dir / "task.yaml"
    try:
        task = yaml.safe_load(task_path.read_text(encoding="utf-8")) or {}
        entries = task["contract"]["participant"]["workspace_allowlist"]
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise WorkspaceError(f"invalid participant workspace declaration: {task_path}") from exc
    if not isinstance(entries, list) or not all(isinstance(item, str) for item in entries):
        raise WorkspaceError(f"workspace_allowlist must be a list of paths: {task_path}")
    if "repo/**" not in entries:
        raise WorkspaceError(f"workspace_allowlist must include repo/**: {task_path}")
    return entries


def _workspace_source(task_dir: Path, declaration: str) -> tuple[Path, PurePosixPath]:
    """Resolve one narrow allowlist declaration without arbitrary globbing."""
    value = declaration.strip()
    recursive = value.endswith("/**")
    relative = value[:-3] if recursive else value
    pure = PurePosixPath(relative)
    if (
        not value
        or pure.is_absolute()
        or ".." in pure.parts
        or "\\" in value
        or any(char in relative for char in "*?[]")
    ):
        raise WorkspaceError(f"unsafe workspace allowlist entry: {declaration!r}")
    root = pure.parts[0]
    if root in _RESERVED_TASK_ROOTS or root.startswith("."):
        raise WorkspaceError(f"evaluator-only path cannot enter workspace: {declaration!r}")
    if root == "repo" and not recursive:
        raise WorkspaceError("repo must be projected recursively as repo/**")
    source = task_dir.joinpath(*pure.parts)
    try:
        source.resolve(strict=False).relative_to(task_dir.resolve())
    except ValueError as exc:
        raise WorkspaceError(f"workspace path escapes task root: {declaration!r}") from exc
    if recursive and source.exists() and not source.is_dir():
        raise WorkspaceError(f"recursive workspace source is not a directory: {source}")
    return source, pure


def _reject_symlinks(source: Path) -> None:
    candidates = [source]
    if source.is_dir():
        candidates.extend(source.rglob("*"))
    leaked = [path for path in candidates if path.is_symlink()]
    if leaked:
        raise WorkspaceError(f"workspace sources may not contain symlinks: {leaked[0]}")


def _reset_directory(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)


def _git(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, errors="replace"
    )
    if check and result.returncode != 0:
        raise WorkspaceError(
            f"git {' '.join(args)} failed in {cwd}:\n{result.stdout}\n{result.stderr}"
        )
    return result


def _ensure_git_repo(repo_dir: Path) -> None:
    if (repo_dir / ".git").exists():
        return
    _git("init", "-q", cwd=repo_dir)
    _git("config", "user.name", "bench-harness", cwd=repo_dir)
    _git("config", "user.email", "bench-harness@localhost", cwd=repo_dir)


def commit_baseline(repo_dir: Path) -> str:
    """Ensure repo_dir is a git repo with everything committed; return the
    baseline commit sha (tagged BASELINE_TAG for readability). Idempotent: a
    second call against an already-clean, already-committed repo returns the
    same sha rather than creating a fresh (timestamp-distinct) empty commit."""
    _ensure_git_repo(repo_dir)
    # Make sure a committer identity exists even if repo pre-existed without one.
    _git("config", "user.name", "bench-harness", cwd=repo_dir, check=False)
    _git("config", "user.email", "bench-harness@localhost", cwd=repo_dir, check=False)

    status = _git("status", "--porcelain", cwd=repo_dir).stdout
    head_exists = _git("rev-parse", "--verify", "HEAD", cwd=repo_dir, check=False).returncode == 0
    if not status.strip() and head_exists:
        _git("tag", "-f", BASELINE_TAG, cwd=repo_dir)
        return _git("rev-parse", "HEAD", cwd=repo_dir).stdout.strip()

    _git("add", "-A", cwd=repo_dir)
    # Commit even if empty diff (first commit in a fresh repo), so we always
    # have a well-known baseline ref.
    _git(
        "commit",
        "--allow-empty",
        "-q",
        "-m",
        "bench: baseline snapshot",
        cwd=repo_dir,
    )
    _git("tag", "-f", BASELINE_TAG, cwd=repo_dir)
    sha = _git("rev-parse", "HEAD", cwd=repo_dir).stdout.strip()
    return sha


def extract_diff(repo_dir: Path, baseline_ref: str = BASELINE_TAG) -> str:
    """Return `git diff <baseline>` text for repo_dir (agent's changes).

    ``--binary`` is REQUIRED, not a nicety. Without it, git emits a placeholder
    line for any binary file ("Binary files a/x and b/x differ") that carries no
    index data, and `git apply --check` then rejects the ENTIRE patch with
    "cannot apply binary patch to 'x' without full index line". The agent's real
    source changes go ungraded and the rollout scores 0.0 — indistinguishable
    from a model that wrote nothing.

    Measured 2026-08-08 on the first live rollout (task-0001, haiku): the agent
    tested its work, which wrote test_output/canonical.db (a SQLite file), and
    that one binary artifact invalidated a 145 KB patch containing correct
    client.py and sync.py edits. Every task whose agent runs the app before
    submitting is exposed, which is most of them.

    Excluding binaries instead would also make the patch apply, but a benchmark
    must not silently drop what the model produced — and the stray artifact is
    inert at grade time (grading runs the app in Docker against its own volumes,
    so a file under the repo's test_output/ is never read).
    """
    _git("add", "-A", cwd=repo_dir)  # stage new/untracked files so they show in diff
    result = _git("diff", "--cached", "--binary", baseline_ref, cwd=repo_dir, check=False)
    if result.returncode not in (0, 1):
        raise WorkspaceError(f"git diff failed:\n{result.stdout}\n{result.stderr}")
    return result.stdout


def prepare_rollout_workspace(task_dir: Path, workspace_dir: Path) -> Path:
    """Create exactly the participant projection declared by ``task.yaml``."""
    task_dir = Path(task_dir).resolve()
    workspace_dir = Path(workspace_dir)
    _reset_directory(workspace_dir)
    copied: set[PurePosixPath] = set()
    for declaration in _declared_workspace_entries(task_dir):
        source, relative = _workspace_source(task_dir, declaration)
        if relative in copied or not source.exists():
            continue
        _reject_symlinks(source)
        destination = workspace_dir.joinpath(*relative.parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination)
        elif source.is_file():
            shutil.copy2(source, destination)
        else:
            raise WorkspaceError(f"unsupported workspace source: {source}")
        copied.add(relative)

    repo_dst = workspace_dir / "repo"
    if not repo_dst.is_dir():
        raise WorkspaceError(f"participant repo was not projected from {task_dir}")
    commit_baseline(repo_dst)
    return repo_dst


def prepare_pristine_repo(task_dir: Path, workspace_dir: Path) -> Path:
    """Fresh pristine copy of repo/ for grading (before patch application)."""
    workspace_dir.mkdir(parents=True, exist_ok=True)
    repo_src = task_dir / "repo"
    repo_dst = workspace_dir / "repo"
    if repo_dst.exists():
        shutil.rmtree(repo_dst)
    shutil.copytree(repo_src, repo_dst)
    commit_baseline(repo_dst)
    return repo_dst


def apply_patch(repo_dir: Path, patch_file: Path) -> tuple[bool, str]:
    """Apply patch_file to repo_dir with `git apply`. Returns (ok, message).
    An unapplyable patch is a normal (non-crashing) outcome per docs/SPEC.md §4
    — callers should treat ok=False as an "unresolved" verdict, not raise.
    """
    # Resolve to an absolute path: `git apply` runs with cwd=repo_dir, so a
    # relative patch path (e.g. the `task_dir/solution.patch` that `bench validate`
    # builds from a relative --task) would otherwise be looked up inside the
    # workspace repo and not found, even though it exists relative to the caller.
    patch_file = Path(patch_file).resolve()
    if not patch_file.is_file():
        return False, f"patch file not found: {patch_file}"
    text = patch_file.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        # Empty patch is a legitimate input (conduct-rules.md gate 2: "empty
        # patch red") — applying nothing always "succeeds".
        return True, "empty patch (no changes)"

    check = subprocess.run(
        ["git", "apply", "--check", str(patch_file)],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        errors="replace",
    )
    if check.returncode != 0:
        return False, f"git apply --check failed:\n{check.stdout}\n{check.stderr}"

    apply_result = subprocess.run(
        ["git", "apply", str(patch_file)],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        errors="replace",
    )
    if apply_result.returncode != 0:
        return False, f"git apply failed:\n{apply_result.stdout}\n{apply_result.stderr}"
    return True, "applied"

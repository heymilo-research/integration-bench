"""Stable provenance captured beside every grade/evaluation attempt."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
from pathlib import Path
from typing import Any

import yaml

from bench.images import load_images_lock, resolve_vendor_image
from bench.scoring import SCORER_VERSION


def _run(*argv: str, cwd: Path | None = None) -> str | None:
    try:
        result = subprocess.run(
            argv, cwd=cwd, capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or result.stderr).strip() or None


def tree_digest(root: Path) -> str:
    """Content digest independent of mtimes, ownership, and checkout path."""
    root = Path(root).resolve()
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if any(
            part in {".git", ".venv", "__pycache__", ".pytest_cache", "output"}
            for part in path.parts
        ):
            continue
        digest.update(rel.encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def git_state(path: Path) -> dict[str, Any]:
    top = _run("git", "rev-parse", "--show-toplevel", cwd=path)
    if not top:
        return {"commit": None, "dirty": None}
    root = Path(top)
    commit = _run("git", "rev-parse", "HEAD", cwd=root)
    status = _run("git", "status", "--porcelain", "--untracked-files=no", cwd=root)
    return {"commit": commit, "dirty": bool(status), "root_name": root.name}


def capture_image_identity(reference: str) -> dict[str, Any]:
    """Resolve a locally present image to stable runtime identity fields."""
    raw = _run("docker", "image", "inspect", reference, "--format", "{{json .}}")
    identity: dict[str, Any] = {
        "reference": reference,
        "image_id": None,
        "repo_digests": [],
        "agent_labels": {},
        "inspection": "unavailable",
    }
    if not raw:
        return identity
    try:
        inspected = json.loads(raw)
    except json.JSONDecodeError:
        identity["inspection"] = "invalid_json"
        return identity
    labels = (inspected.get("Config") or {}).get("Labels") or {}
    identity.update(
        {
            "image_id": inspected.get("Id"),
            "repo_digests": sorted(inspected.get("RepoDigests") or []),
            "agent_labels": {
                str(key): str(value)
                for key, value in sorted(labels.items())
                if str(key).startswith("ib.agent.")
            },
            "inspection": "resolved",
        }
    )
    return identity


def _requested_reasoning_effort() -> str:
    effort = os.environ.get("IB_REASONING_EFFORT", "").strip().lower()
    return effort or "unspecified"


def _effective_reasoning_effort(provider: str, requested: str) -> tuple[str, str]:
    if requested == "xhigh":
        if provider == "claude-code":
            return "xhigh", "claude --effort xhigh"
        if provider == "codex":
            return "xhigh", 'codex --config model_reasoning_effort="xhigh"'
        if provider == "opencode":
            return "xhigh", "opencode --variant xhigh"
        return "xhigh", "IB_REASONING_EFFORT=xhigh"
    if provider == "opencode" and requested != "unspecified":
        return requested, f"opencode --variant {requested}"
    if requested in {"unspecified", "claude-code-model-default", "codex-config-pinned"}:
        return "unverified-provider-default", "no explicit effort override"
    return requested, "explicit non-primary effort override"


def capture_provenance(
    task_dir: Path,
    *,
    model: str,
    provider: str,
    mode: str,
    requested_effort: str | None = None,
    resolve_images: bool = True,
) -> dict[str, Any]:
    """Capture pins needed to reproduce or reject a mixed-revision result."""
    requested_effort = requested_effort or _requested_reasoning_effort()
    effective_effort, effort_source = _effective_reasoning_effort(provider, requested_effort)
    require_xhigh = os.environ.get("IB_REQUIRE_XHIGH_EFFORT", "").strip() == "1"
    if require_xhigh and effective_effort != "xhigh":
        raise RuntimeError(
            "primary clean-rerun protocol requires effective reasoning effort "
            f"xhigh; requested={requested_effort!r}, effective={effective_effort!r}"
        )

    task_dir = Path(task_dir).resolve()
    harness_dir = Path(__file__).resolve().parents[1]
    task_data = yaml.safe_load((task_dir / "task.yaml").read_text(encoding="utf-8"))
    runtime_contract = (task_data.get("contract") or {}).get("runtime") or {}
    runtime_data = json.dumps(runtime_contract, sort_keys=True, separators=(",", ":"))
    repo_root = harness_dir.parent
    lock_path = repo_root / "images.lock.json"
    lock_data = load_images_lock(repo_root)
    image_pins: dict[str, Any] = {}
    images: list[str] = []
    for role, metadata in (task_data.get("vendors") or {}).items():
        declared = ((task_data.get("contract") or {}).get("vendor_roles") or {}).get(role) or {}
        product = str(declared.get("vendor_id") or metadata.get("vendor_id") or role)
        if not resolve_images:
            images.append(product)
            image_pins[role] = {
                "vendor_id": product,
                "resolution": "not_started",
            }
            continue
        resolved = resolve_vendor_image(product, repo_root=repo_root)
        images.append(resolved)
        inspected = _run(
            "docker",
            "image",
            "inspect",
            resolved,
            "--format",
            "{{json .RepoDigests}}|{{.Id}}",
        )
        image_pins[role] = {
            "vendor_id": product,
            "resolved": resolved,
            "inspect": inspected,
            "lock": lock_data["vendors"][product],
        }
    profile_path = Path(
        os.environ.get("IB_CLAUDE_WORK")
        or os.environ.get("IB_CODEX_HOME")
        or os.environ.get("CODEX_HOME")
        or "unspecified"
    )
    config_hashes = {}
    if profile_path.is_dir():
        for name in ("config.toml", "settings.json", ".claude.json"):
            config = profile_path / name
            if config.is_file():
                config_hashes[name] = hashlib.sha256(config.read_bytes()).hexdigest()
    return {
        "schema": 1,
        "scorer_version": SCORER_VERSION,
        "task": task_dir.name,
        "task_tree_sha256": tree_digest(task_dir),
        "task_git": git_state(task_dir),
        "harness_tree_sha256": tree_digest(harness_dir),
        "harness_git": git_state(harness_dir),
        "model": model,
        "provider": provider,
        "mode": mode,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "docker": _run("docker", "--version"),
        "docker_compose": _run("docker", "compose", "version"),
        "claude_cli": _run("claude", "--version"),
        "codex_cli": _run("codex", "--version"),
        "opencode_cli": _run("opencode", "--version"),
        "agent_home_policy": "fresh-minimal-per-attempt",
        "credential_profile": profile_path.name,
        "agent_config_sha256": config_hashes,
        "reasoning_effort": effective_effort,
        "requested_reasoning_effort": requested_effort,
        "effective_reasoning_effort": effective_effort,
        "reasoning_effort_source": effort_source,
        "reasoning_effort_required_xhigh": require_xhigh,
        "seed": os.environ.get("IB_EVAL_SEED", "unspecified"),
        "max_infrastructure_attempts": os.environ.get("IB_MAX_INFRA_ATTEMPTS", "2"),
        "tool_policy": os.environ.get("IB_TOOL_POLICY", "harness-default"),
        "runtime_contract_sha256": hashlib.sha256(runtime_data.encode()).hexdigest(),
        "images_lock_sha256": hashlib.sha256(lock_path.read_bytes()).hexdigest(),
        "image_resolution": "resolved" if resolve_images else "not_started",
        "compose_images": images,
        "image_pins": image_pins,
    }


def canonical_provenance_json(value: dict[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))

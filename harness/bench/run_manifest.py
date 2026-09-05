"""Build the immutable, schema-versioned manifest for one retained run."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
TERMINAL_STATUSES = {
    "done",
    "error",
    "patch_failed",
    "provider_error",
    "usage_limit",
}
_EXCLUDED_ARTIFACT_ROOTS = {"agent-home", "workspace"}
_EXCLUDED_ARTIFACT_FILES = {"meta.json", "run-manifest.json"}


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _iso8601(epoch: Any) -> str:
    try:
        value = float(epoch)
    except (TypeError, ValueError):
        value = 0.0
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _harness_version() -> str:
    try:
        return version("integration-bench-harness")
    except PackageNotFoundError:
        return "unknown"


def _protocol_version() -> str:
    path = REPO_ROOT / "evaluations" / "protocols" / "v1.yaml"
    if not path.is_file():
        return "unknown"
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return str(data.get("id") or data.get("schema_version") or "unknown")


def artifact_hashes(eval_root: Path) -> dict[str, str]:
    """Hash retained artifacts without traversing agent state or workspaces."""
    hashes: dict[str, str] = {}
    for path in sorted(p for p in Path(eval_root).rglob("*") if p.is_file()):
        rel = path.relative_to(eval_root)
        if rel.parts[0] in _EXCLUDED_ARTIFACT_ROOTS:
            continue
        if rel.as_posix() in _EXCLUDED_ARTIFACT_FILES:
            continue
        hashes[rel.as_posix()] = file_sha256(path)
    return hashes


def _seed(provenance: dict[str, Any]) -> tuple[int, str]:
    raw = provenance.get("seed", "unspecified")
    try:
        return int(raw), str(raw)
    except (TypeError, ValueError):
        return 0, str(raw)


def build_run_manifest(eval_root: Path, meta: dict[str, Any]) -> dict[str, Any]:
    """Return a contract-valid manifest from final metadata and artifacts."""
    provenance = meta.get("rollout_provenance") or meta.get("provenance") or {}
    if not isinstance(provenance, dict):
        raise ValueError("final run metadata has no provenance object")

    task_hash = str(provenance.get("task_tree_sha256") or "")
    image_lock_hash = str(provenance.get("images_lock_sha256") or "")
    if len(task_hash) != 64 or len(image_lock_hash) != 64:
        raise ValueError("run provenance is missing task or image-lock SHA-256")

    catalog_path = REPO_ROOT / "tasks" / "catalog.json"
    if not catalog_path.is_file():
        raise ValueError("tasks/catalog.json is required to finalize a run manifest")

    harness_git = provenance.get("harness_git") or {}
    task_git = provenance.get("task_git") or {}
    source_revision = str(
        harness_git.get("commit")
        or task_git.get("commit")
        or provenance.get("harness_tree_sha256")
        or "unknown"
    )
    if len(source_revision) < 7:
        raise ValueError("run provenance has no usable source revision")

    images: dict[str, Any] = {}
    for role, identity in sorted((provenance.get("image_pins") or {}).items()):
        images[str(role)] = identity
    agent_image = provenance.get("agent_image")
    if isinstance(agent_image, dict):
        images["agent"] = agent_image
    if not images:
        raise ValueError("run provenance has no vendor or agent image identities")

    seed, raw_seed = _seed(provenance)
    hashes = artifact_hashes(eval_root)
    model_configuration = {
        key: value
        for key, value in {
            "auth_mode": meta.get("auth_mode"),
            "reasoning_effort": provenance.get("effective_reasoning_effort"),
            "requested_reasoning_effort": provenance.get("requested_reasoning_effort"),
            "tool_policy": provenance.get("tool_policy"),
            "max_turns": meta.get("max_turns"),
            "timeout_minutes": meta.get("timeout_minutes"),
            "opencode_variant": meta.get("opencode_variant"),
            "agent_command_sha256": meta.get("agent_command_sha256"),
        }.items()
        if value is not None
    }
    return {
        "schema_version": 1,
        "run_id": str(meta.get("run_id") or meta.get("eval_id")),
        "attempt_id": str(meta.get("attempt_id") or meta.get("eval_id")),
        "logical_rollout_id": str(meta.get("logical_rollout_id") or meta.get("eval_id")),
        "task": str(meta.get("task") or provenance.get("task")),
        "source_revision": source_revision,
        "source_dirty": bool(harness_git.get("dirty") or task_git.get("dirty")),
        "source_tree_hash": provenance.get("harness_tree_sha256"),
        "task_hash": task_hash,
        "catalog_hash": file_sha256(catalog_path),
        "image_lock_hash": image_lock_hash,
        "images": images,
        "harness_version": _harness_version(),
        "harness_hash": provenance.get("harness_tree_sha256"),
        "contract_versions": {
            "task": "v1",
            "vendor": "v1",
            "verdict": "v1",
            "run_manifest": "v1",
            "scorer": str(meta.get("scorer_version") or provenance.get("scorer_version")),
        },
        "protocol_version": _protocol_version(),
        "model": {
            "name": str(meta.get("model") or provenance.get("model") or "unknown"),
            "provider": str(meta.get("provider") or provenance.get("provider") or "unknown"),
            "harness": str(
                meta.get("harness") or meta.get("mode") or provenance.get("mode") or "unknown"
            ),
            "configuration": model_configuration,
        },
        "seed": seed,
        "seed_source": raw_seed,
        "started_at": _iso8601(meta.get("created_at")),
        "ended_at": _iso8601(meta.get("updated_at")),
        "timing": meta.get("timing") or {"elapsed_s": meta.get("elapsed_s")},
        "status": str(meta.get("status")),
        "failure_class": meta.get("failure_class"),
        "outcome": {
            "resolved": meta.get("resolved"),
            "task_score": meta.get("task_score"),
            "reward": meta.get("reward"),
            "error": meta.get("error"),
        },
        "artifact_hashes": hashes,
        "artifact_locations": {name: name for name in hashes},
    }


def write_run_manifest(eval_root: Path, meta: dict[str, Any]) -> Path:
    path = Path(eval_root) / "run-manifest.json"
    document = build_run_manifest(eval_root, meta)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path

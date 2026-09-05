"""Credential value resolution from the canonical task contract."""

from __future__ import annotations

from pathlib import Path
import yaml


def resolve_credential_env(
    task_dir: Path, credential_names: list[str], service: str = "vendor"
) -> dict[str, str]:
    """Return synthetic credential values declared by the task contract."""
    task_path = task_dir / "task.yaml"
    if task_path.is_file():
        task = yaml.safe_load(task_path.read_text(encoding="utf-8")) or {}
        vendors = task.get("vendors") or {}
        metadata = vendors.get(service)
        if metadata is None and len(vendors) == 1:
            metadata = next(iter(vendors.values()))
        if metadata is not None:
            credentials = metadata.get("credentials") or {}
            return {
                name: str(credentials[name]) for name in credential_names if name in credentials
            }
    return {}

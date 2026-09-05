"""Parsing for task.yaml under the M1 file-and-env vendor contract.

The harness no longer reads vendor.yaml; per-task metadata in task.yaml carries
vendors, credentials, entry command, declared outputs, and entity kinds.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import yaml


class ConfigError(RuntimeError):
    """Raised when a task config file is missing or malformed."""


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigError(f"missing config file: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"expected a YAML mapping at top level: {path}")
    return data


def _ensure_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


@dataclasses.dataclass
class VendorMetadata:
    """Per-vendor metadata declared in task.yaml."""

    name: str
    vendor_id: str
    data_port: int
    log_path: str
    checkpoint_env: str
    checkpoint: int
    credentials: dict[str, str]
    token_endpoint: str | None
    token_ttl: int | None
    entities: dict[str, dict[str, Any]]
    raw: dict[str, Any]

    @property
    def product(self) -> str:
        """Stable vendor ID; image selection belongs exclusively to the lock."""
        return self.vendor_id

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "VendorMetadata":
        return cls(
            name=name,
            vendor_id=str(data.get("vendor_id") or name),
            data_port=int(data.get("data_port", 8000)),
            log_path=data.get("log_path", "/var/log/vendor"),
            checkpoint_env=data.get("checkpoint_env", "CHECKPOINT"),
            checkpoint=int(data.get("checkpoint", 0)),
            credentials=dict(data.get("credentials", {}) or {}),
            token_endpoint=data.get("auth", {}).get("token_endpoint") if data.get("auth") else None,
            token_ttl=data.get("auth", {}).get("token_ttl") if data.get("auth") else None,
            entities=dict(data.get("entities", {}) or {}),
            raw=data,
        )


@dataclasses.dataclass
class TaskConfig:
    """Parsed task.yaml."""

    id: str
    title: str
    category: str
    vendor: str
    surfaces: list[str]
    tier: int
    track: str
    timeout_minutes: int
    doc_profile: str
    scenarios: list[str] | None
    l3_scenarios: list[str]
    entry: list[str]
    outputs: dict[str, Any]
    vendors: dict[str, VendorMetadata]
    raw: dict[str, Any]

    @classmethod
    def load(cls, task_dir: Path) -> "TaskConfig":
        data = _load_yaml(task_dir / "task.yaml")
        try:
            vendors_raw = data.get("vendors", {}) or {}
            vendors = {name: VendorMetadata.from_dict(name, v) for name, v in vendors_raw.items()}
            contract_roles = (data.get("contract") or {}).get("vendor_roles") or {}
            for name, vendor in vendors.items():
                declared = contract_roles.get(name) or {}
                vendor.vendor_id = str(declared.get("vendor_id") or vendor.vendor_id)
                vendor.checkpoint = int(declared.get("checkpoint", vendor.checkpoint))
            default_vendor = data.get("vendor")
            # Multi-vendor tasks (M2, SPEC §5.3 — e.g. migrate tasks with
            # `vendor-legacy`/`vendor-new` service blocks) name their `vendor:`
            # field after the vendor *product* (e.g. "placemint"), which is
            # informational only and does not need to match a literal
            # `vendors:` block key. Only enforce the match for single-vendor
            # tasks, where `vendor:` IS the lookup key used elsewhere.
            if default_vendor and len(vendors) <= 1 and default_vendor not in vendors:
                raise ConfigError(f"default vendor {default_vendor!r} not found in vendors block")

            entry = data.get("entry", {}).get("command", [])
            if isinstance(entry, str):
                entry = entry.split()

            return cls(
                id=data["id"],
                title=data.get("title", ""),
                category=data["category"],
                vendor=default_vendor or "",
                surfaces=list(data.get("surfaces", [])),
                tier=int(data.get("tier", 1)),
                track=data.get("track", "python"),
                timeout_minutes=int(data.get("timeout_minutes", 60)),
                doc_profile=str(data.get("doc_profile", "true")),
                scenarios=list(data["scenarios"]) if "scenarios" in data else None,
                l3_scenarios=list(data.get("l3_scenarios", [])),
                entry=list(entry),
                outputs=dict(data.get("outputs", {}) or {}),
                vendors=vendors,
                raw=data,
            )
        except KeyError as exc:
            raise ConfigError(f"task.yaml missing required field: {exc}") from exc


def load_task_config(task_dir: Path) -> TaskConfig:
    return TaskConfig.load(task_dir)

"""Durable sync state between invocations (provided). See ``PROBLEM.md``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bullpen_migrate.config import ENTITIES


def load_state(path: Path) -> dict[str, Any]:
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data.setdefault("auth_mode", "legacy")
            data.setdefault("watermarks", {})
            for kind in ENTITIES:
                data["watermarks"].setdefault(kind, None)
            return data
        except json.JSONDecodeError:
            pass
    return {"auth_mode": "legacy", "watermarks": {kind: None for kind in ENTITIES}}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")

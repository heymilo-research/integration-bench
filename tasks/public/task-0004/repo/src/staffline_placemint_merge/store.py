"""JSON persistence under OUTPUT_DIR. Output shapes are in PROBLEM.md. COMPLETE."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json(path: Path, default: Any) -> Any:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default
    return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def write_roster(path: Path, rows: list[dict[str, Any]]) -> None:
    write_json(path, sorted(rows, key=lambda r: r["source_id"]))


def write_corrections(path: Path, rows: list[dict[str, Any]]) -> None:
    write_json(path, sorted(rows, key=lambda r: r["candidate_id"]))

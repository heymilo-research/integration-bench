"""Canonical store. See ``PROBLEM.md``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_existing(path: Path) -> list[dict[str, Any]]:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []


def merge_rows(existing: list[dict[str, Any]], new_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {row["source_id"]: row for row in existing}
    for row in new_rows:
        by_id[row["source_id"]] = row
    return sorted(by_id.values(), key=lambda r: r["source_id"])


def write_store(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2, sort_keys=False), encoding="utf-8")


def load_json(path: Path, default: Any) -> Any:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return default
    return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")

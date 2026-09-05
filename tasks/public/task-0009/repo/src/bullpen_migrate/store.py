"""Canonical store (provided). See ``PROBLEM.md``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _to_canonical_row(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": rec["source_id"],
        "data": {k: v for k, v in rec.items() if k not in ("id", "source_id")},
        "is_deleted": rec["is_deleted"],
        "updated_at": rec["modified_at"],
    }


def merge_rows(existing: list[dict[str, Any]], new_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {row["source_id"]: row for row in existing}
    for rec in new_records:
        by_id[rec["source_id"]] = _to_canonical_row(rec)
    return sorted(by_id.values(), key=lambda r: r["source_id"])


def read_existing(path: Path) -> list[dict[str, Any]]:
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return []
    return []


def write_store(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, indent=2, sort_keys=False), encoding="utf-8")

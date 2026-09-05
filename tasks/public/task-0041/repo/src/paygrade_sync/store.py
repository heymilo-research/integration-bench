"""Canonical store (provided). See ``PROBLEM.md``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_STATE_FILE = ".sync_state.json"


class Table:
    """A tiny JSON-file-backed row table, keyed by ``source_id``."""

    def __init__(self, output_dir: Path, filename: str) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._path = self.output_dir / filename
        self._rows: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if self._path.is_file():
            try:
                rows = json.loads(self._path.read_text(encoding="utf-8"))
                for r in rows:
                    self._rows[r["source_id"]] = r
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                self._rows = {}

    def flush(self) -> None:
        rows = [self._rows[k] for k in sorted(self._rows.keys())]
        self._path.write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")

    def get_row(self, source_id: str) -> dict[str, Any] | None:
        return self._rows.get(source_id)

    def upsert(
        self, *, source_id: str, data: dict[str, Any], updated_at: int, is_deleted: bool = False
    ) -> None:
        self._rows[source_id] = {
            "source_id": source_id,
            "data": data,
            "updated_at": int(updated_at),
            "is_deleted": bool(is_deleted),
        }

    def tombstone(self, *, source_id: str) -> None:
        """Mark a row deleted in place, retaining its last-known data."""
        existing = self._rows.get(source_id)
        if existing is None:
            self._rows[source_id] = {
                "source_id": source_id,
                "data": {},
                "updated_at": 0,
                "is_deleted": True,
            }
            return
        existing["is_deleted"] = True

    def rows(self) -> list[dict[str, Any]]:
        return [self._rows[k] for k in sorted(self._rows.keys())]


class Store:
    """Canonical store: employees, assignments, and watermark state."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.employees = Table(self.output_dir, "employees.json")
        self.assignments = Table(self.output_dir, "assignments.json")
        self._state: dict[str, Any] = {}
        self._state_path = self.output_dir / _STATE_FILE
        if self._state_path.is_file():
            try:
                self._state = json.loads(self._state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._state = {}

    def flush(self) -> None:
        self.employees.flush()
        self.assignments.flush()
        self._state_path.write_text(json.dumps(self._state, sort_keys=True), encoding="utf-8")

    def get_state(self, key: str) -> Any | None:
        return self._state.get(key)

    def set_state(self, key: str, value: Any) -> None:
        self._state[key] = value

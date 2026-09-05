"""Canonical store (provided). See ``PROBLEM.md``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

STATE_FILE = "state.json"


class Store:
    """A directory of canonical entity files plus a sync-state file."""

    def __init__(self, output_dir: Path) -> None:
        self._dir = Path(output_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- entities
    def _entity_path(self, table: str) -> Path:
        return self._dir / f"{table}.json"

    def load(self, table: str) -> dict[str, dict[str, Any]]:
        """Load an entity table as ``{source_id: canonical_row}`` (empty if new)."""
        path = self._entity_path(table)
        if not path.is_file():
            return {}
        rows = json.loads(path.read_text(encoding="utf-8"))
        return {row["source_id"]: row for row in rows}

    def write(self, table: str, rows: dict[str, dict[str, Any]]) -> None:
        """Persist an entity table, sorted by ``source_id`` for stable output."""
        ordered = [rows[k] for k in sorted(rows)]
        path = self._entity_path(table)
        path.write_text(json.dumps(ordered, indent=2, sort_keys=False), encoding="utf-8")

    @staticmethod
    def upsert(
        rows: dict[str, dict[str, Any]],
        source_id: str,
        data: dict[str, Any],
        updated_at: Any,
        is_deleted: bool,
    ) -> None:
        """Insert or replace a canonical row in-place."""
        rows[source_id] = {
            "source_id": source_id,
            "data": data,
            "updated_at": updated_at,
            "is_deleted": is_deleted,
        }

    # ------------------------------------------------------------------- state
    def _state(self) -> dict[str, Any]:
        path = self._dir / STATE_FILE
        if not path.is_file():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def get_state(self, key: str) -> Any | None:
        return self._state().get(key)

    def set_state(self, key: str, value: Any) -> None:
        state = self._state()
        state[key] = value
        path = self._dir / STATE_FILE
        path.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")

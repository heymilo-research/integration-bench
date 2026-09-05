"""Canonical store (provided). See ``PROBLEM.md``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_STORE_FILE = "candidates.json"
_STATE_FILE = ".poll_state.json"


class Store:
    """A tiny JSON-file-backed candidate store, keyed by ``source_id``."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._rows: dict[str, dict[str, Any]] = {}
        self._state: dict[str, Any] = {}
        self._load()

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        store_path = self.output_dir / _STORE_FILE
        if store_path.is_file():
            try:
                rows = json.loads(store_path.read_text(encoding="utf-8"))
                for r in rows:
                    self._rows[r["source_id"]] = r
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                self._rows = {}
        state_path = self.output_dir / _STATE_FILE
        if state_path.is_file():
            try:
                self._state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._state = {}

    def flush(self) -> None:
        """Write the canonical store to ``output/candidates.json`` (sorted by
        ``source_id``) and persist the watermark state alongside it."""
        rows = [self._rows[k] for k in sorted(self._rows.keys())]
        (self.output_dir / _STORE_FILE).write_text(
            json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8"
        )
        (self.output_dir / _STATE_FILE).write_text(
            json.dumps(self._state, sort_keys=True), encoding="utf-8"
        )

    # -- rows ---------------------------------------------------------------

    def get_row(self, source_id: str) -> dict[str, Any] | None:
        return self._rows.get(source_id)

    def upsert(
        self,
        *,
        source_id: str,
        data: dict[str, Any],
        updated_at: int,
        is_deleted: bool = False,
    ) -> None:
        self._rows[source_id] = {
            "source_id": source_id,
            "data": data,
            "updated_at": int(updated_at),
            "is_deleted": bool(is_deleted),
        }

    def tombstone(
        self, *, source_id: str, data: dict[str, Any], updated_at: int
    ) -> None:
        """Retain a deleted candidate as a tombstone row (is_deleted=True)."""
        self.upsert(
            source_id=source_id, data=data, updated_at=updated_at, is_deleted=True
        )

    def rows(self) -> list[dict[str, Any]]:
        return [self._rows[k] for k in sorted(self._rows.keys())]

    # -- watermark / state --------------------------------------------------

    def get_state(self, key: str) -> Any | None:
        return self._state.get(key)

    def set_state(self, key: str, value: Any) -> None:
        self._state[key] = value

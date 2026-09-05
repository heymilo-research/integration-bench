"""Canonical store. See ``PROBLEM.md``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_ENTITY_FILES = {
    "worker": "workers.json",
    "gig": "gigs.json",
    "assignment": "assignments.json",
}


class Store:
    """A tiny JSON-file-backed store per entity, each keyed by ``source_id``."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._rows: dict[str, dict[str, dict[str, Any]]] = {
            entity: {} for entity in _ENTITY_FILES
        }

    def get_row(self, entity: str, source_id: str) -> dict[str, Any] | None:
        return self._rows[entity].get(source_id)

    def upsert(
        self,
        entity: str,
        *,
        source_id: str,
        data: dict[str, Any],
        updated_at: str,
        is_deleted: bool = False,
    ) -> None:
        self._rows[entity][source_id] = {
            "source_id": source_id,
            "data": data,
            "updated_at": updated_at,
            "is_deleted": bool(is_deleted),
        }

    def tombstone(
        self, entity: str, *, source_id: str, data: dict[str, Any], updated_at: str
    ) -> None:
        self.upsert(
            entity, source_id=source_id, data=data, updated_at=updated_at, is_deleted=True
        )

    def rows(self, entity: str) -> list[dict[str, Any]]:
        return [self._rows[entity][k] for k in sorted(self._rows[entity].keys())]

    def flush(self) -> None:
        """Write every entity's canonical rows to ``output/<plural>.json``,
        sorted by ``source_id`` for byte-stable snapshots."""
        for entity, filename in _ENTITY_FILES.items():
            out = self.output_dir / filename
            out.write_text(
                json.dumps(self.rows(entity), indent=2, sort_keys=True), encoding="utf-8"
            )

    def load(self) -> None:
        """Load any previously-flushed rows back in (best-effort)."""
        for entity, filename in _ENTITY_FILES.items():
            path = self.output_dir / filename
            if not path.is_file():
                continue
            try:
                rows = json.loads(path.read_text(encoding="utf-8"))
                for r in rows:
                    self._rows[entity][r["source_id"]] = r
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                continue

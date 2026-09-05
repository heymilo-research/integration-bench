"""The warehouse snapshot the previous run left behind.

The file is read-only input: one entry per collection, keyed by record id, each
carrying the record's canonical value and the ``updated_at`` the warehouse
believes it holds. ``synced_through`` is the position the previous run recorded
for itself when it finished.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The one field the warehouse keeps for each kind of record, and therefore the
# field parity is judged on.
CANONICAL_FIELD = {"candidates": "status", "jobs": "status", "applications": "stage"}
ENTITY_NAME = {"candidates": "candidate", "jobs": "job", "applications": "application"}
COLLECTIONS = ("candidates", "jobs", "applications")


@dataclass
class Snapshot:
    synced_through: str
    collections: dict[str, dict[str, dict[str, Any]]]

    def held(self, collection: str) -> dict[str, dict[str, Any]]:
        return self.collections.setdefault(collection, {})

    def newest_updated_at(self) -> str:
        stamps = [
            str(rec.get("updated_at") or "")
            for rows in self.collections.values()
            for rec in rows.values()
        ]
        return max(stamps) if stamps else ""


def read_snapshot(path: Path) -> Snapshot:
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    collections = doc.get("collections") or {}
    return Snapshot(
        synced_through=str(doc.get("synced_through") or ""),
        collections={
            name: {str(k): dict(v) for k, v in (collections.get(name) or {}).items()}
            for name in COLLECTIONS
        },
    )

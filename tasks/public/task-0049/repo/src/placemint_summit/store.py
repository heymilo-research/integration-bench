"""Canonical store (provided). See ``PROBLEM.md``."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

_KINDS = ("placements", "clients", "notes")


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {
            "placements": {},
            "clients": {},
            "notes": {},
            "watermarks": {"placements": "", "clients": "", "notes": ""},
            "seen_event_ids": [],
        }
        self._load()

    # -- persistence ---------------------------------------------------

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for kind in _KINDS:
            self._data[kind] = raw.get(kind, {})
        self._data["watermarks"] = raw.get("watermarks", {k: "" for k in _KINDS})
        self._data["seen_event_ids"] = raw.get("seen_event_ids", [])

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.path)

    # -- canonical apply (shared by poll + webhook paths) ---------------

    def apply_record(self, kind: str, record: dict[str, Any]) -> bool:
        """Upsert ``record`` into collection ``kind``.

        Returns True if written, False if skipped as stale (by ``updated_at``).
        """
        with self._lock:
            coll = self._data[kind]
            source_id = record["id"]
            existing = coll.get(source_id)
            new_updated = str(record.get("updated_at", ""))
            if existing is not None and new_updated < str(existing.get("updated_at", "")):
                return False
            coll[source_id] = dict(record)
            return True

    def get(self, kind: str, source_id: str) -> dict[str, Any] | None:
        with self._lock:
            return self._data[kind].get(source_id)

    def all_rows(self, kind: str) -> list[dict[str, Any]]:
        with self._lock:
            return sorted(self._data[kind].values(), key=lambda r: r["id"])

    # -- watermarks (per-entity, used by poll.py for modified_since) -----

    def get_watermark(self, kind: str) -> str:
        with self._lock:
            return self._data["watermarks"].get(kind, "")

    def set_watermark(self, kind: str, value: str) -> None:
        with self._lock:
            if value > self._data["watermarks"].get(kind, ""):
                self._data["watermarks"][kind] = value

    # -- webhook event dedup ---------------------------------------------

    def event_seen(self, event_id: str) -> bool:
        with self._lock:
            return event_id in self._data["seen_event_ids"]

    def mark_event_seen(self, event_id: str) -> None:
        with self._lock:
            if event_id not in self._data["seen_event_ids"]:
                self._data["seen_event_ids"].append(event_id)


def canonical_row(record: dict[str, Any]) -> dict[str, Any]:
    """Map a raw vendor record to the canonical output row shape."""
    return {
        "source_id": record.get("source_id") or record["id"],
        "data": {k: v for k, v in record.items() if k != "source_id"},
        "is_deleted": bool(record.get("is_deleted", False)),
        "updated_at": str(record.get("updated_at", "")),
    }


def canonical_rows(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records = sorted(records, key=lambda r: r["id"])
    return [canonical_row(r) for r in records]


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, sort_keys=False)

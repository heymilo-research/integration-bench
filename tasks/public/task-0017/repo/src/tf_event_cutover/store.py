"""The canonical mirror, the change ledger and our own run state.

Everything lives under ``OUTPUT_DIR``, which is mounted, so it survives the
one-shot container exits between passes. The OUTPUT SHAPES below are the
warehouse's contract and are already written correctly -- ``flush()`` needs no
changes.

    mirror.json        {"row_count": <int>, "rows": [<mirror row>, ...]}
    change_ledger.json {"row_count": <int>, "rows": [<ledger row>, ...]}
    state.json         {"watermark": <str|null>, "runs": <int>}

A mirror row::

    {"source_id": "XX-0000", "given_name": "<str>", "family_name": "<str>",
     "email": "someone@example.invalid", "phone": "<str>",
     "pipeline_status": "<str>", "is_deleted": false,
     "updated_at": "2000-01-01T00:00:00Z"}

A ledger row::

    {"event_id": "evt_00000", "event": "<str>", "candidate_id": "XX-0000",
     "occurred_at": "2000-01-01T00:00:00Z", "pipeline_status": "<str>",
     "updated_at": "2000-01-01T00:00:00Z"}

``upsert_from_vendor`` is keyed on the record's id, so re-reading a record we
already hold replaces it in place rather than duplicating it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

MIRROR_FILE = "mirror.json"
LEDGER_FILE = "change_ledger.json"
STATE_FILE = "state.json"

MIRROR_FIELDS = (
    "given_name",
    "family_name",
    "email",
    "phone",
    "pipeline_status",
)

# docs/entities.md, Candidate: the last-modified stamp is `updatedAt`, typed
# `string (ISO 8601)`. `created_at` is the one candidate field the same table
# types as an integer.
UPDATED_FIELD = "updatedAt"


def normalise_updated_at(record: dict[str, Any]) -> str | None:
    """The warehouse's `updated_at` column for one vendor record.

    The column is UTC ISO-8601 to the second with a trailing `Z`
    (`YYYY-MM-DDTHH:MM:SSZ`) -- that is what every downstream report has
    bucketed on since the file era, so whatever the wire hands us has to end up
    in that shape.
    """
    value = record.get(UPDATED_FIELD)
    if isinstance(value, str) and value:
        return value
    return None


class Store:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._rows: dict[str, dict[str, Any]] = {}
        self._ledger: dict[str, dict[str, Any]] = {}
        self._state: dict[str, Any] = {"watermark": None, "runs": 0}
        self._load()

    # -- persistence --------------------------------------------------------

    def _load(self) -> None:
        for name, sink in ((MIRROR_FILE, self._rows), (LEDGER_FILE, self._ledger)):
            path = self.output_dir / name
            if not path.is_file():
                continue
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                continue
            key = "source_id" if name == MIRROR_FILE else "event_id"
            for row in doc.get("rows") or []:
                if row.get(key):
                    sink[row[key]] = row
        state_path = self.output_dir / STATE_FILE
        if state_path.is_file():
            try:
                loaded = json.loads(state_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._state.update(loaded)
            except (ValueError, OSError):
                pass

    def flush(self) -> None:
        rows = sorted(self._rows.values(), key=lambda r: r["source_id"])
        ledger = sorted(self._ledger.values(), key=lambda r: r["event_id"])
        (self.output_dir / MIRROR_FILE).write_text(
            json.dumps({"row_count": len(rows), "rows": rows}, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        (self.output_dir / LEDGER_FILE).write_text(
            json.dumps(
                {"row_count": len(ledger), "rows": ledger}, indent=2, sort_keys=True
            ),
            encoding="utf-8",
        )
        (self.output_dir / STATE_FILE).write_text(
            json.dumps(self._state, indent=2, sort_keys=True), encoding="utf-8"
        )

    # -- mirror -------------------------------------------------------------

    def upsert_from_vendor(self, record: dict[str, Any]) -> dict[str, Any] | None:
        """Write one record TalentForge served into the mirror."""
        sid = record.get("id") or record.get("source_id")
        if not sid:
            return None
        row = {"source_id": sid}
        for field in MIRROR_FIELDS:
            row[field] = record.get(field)
        row["is_deleted"] = bool(record.get("is_deleted", False))
        row["updated_at"] = normalise_updated_at(record)
        self._rows[sid] = row
        return row

    def row(self, source_id: str) -> dict[str, Any] | None:
        return self._rows.get(source_id)

    def rows(self) -> list[dict[str, Any]]:
        return sorted(self._rows.values(), key=lambda r: r["source_id"])

    def count(self) -> int:
        return len(self._rows)

    # -- ledger -------------------------------------------------------------

    def record_change(self, ledger_row: dict[str, Any]) -> None:
        """One row per event id. A re-delivery overwrites rather than appends."""
        if ledger_row.get("event_id"):
            self._ledger[ledger_row["event_id"]] = ledger_row

    def ledger(self) -> list[dict[str, Any]]:
        return sorted(self._ledger.values(), key=lambda r: r["event_id"])

    # -- state --------------------------------------------------------------

    def watermark(self) -> str | None:
        value = self._state.get("watermark")
        return value if isinstance(value, str) and value else None

    def set_watermark(self, value: str | None) -> None:
        self._state["watermark"] = value

    def note_run(self) -> int:
        self._state["runs"] = int(self._state.get("runs", 0)) + 1
        return self._state["runs"]

    def put_row(self, row: dict[str, Any]) -> None:
        """Write one mirror row verbatim; its ``source_id`` is the key."""
        if row.get("source_id"):
            self._rows[str(row["source_id"])] = dict(row)

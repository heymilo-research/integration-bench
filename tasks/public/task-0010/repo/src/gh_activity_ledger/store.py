"""Artifact writers for the ledger.

Two files land in ``OUTPUT_DIR``. Every value in them is derived from the
:class:`~gh_activity_ledger.ledger.LedgerResult` handed in; this module formats
and counts, it decides nothing.

``activity_ledger.csv`` is what Compliance reads: a header row plus one row per
record the ledger placed in a period, ordered by period (calendar order), then
entity, then ``record_id``::

    window_id,entity,record_id,outcome
    W0,candidates,XX_00000,created

``result.json`` is the summary the audit pack embeds. ``per_window`` counts what
the ledger placed in each period; ``tenant`` is how many records the run read
off GlobalHire; ``outside_windows`` is how many of those the ledger placed in no
period at all::

    {"per_window": [{"window_id": "W0",
                     "candidates": {"total": 0, "live": 0, "deleted": 0},
                     "placements": {"total": 0, "live": 0, "deleted": 0},
                     "agencies":   {"total": 0, "live": 0, "deleted": 0}}],
     "tenant": {"candidates": 0, "placements": 0, "agencies": 0},
     "outside_windows": {"candidates": 0, "placements": 0, "agencies": 0}}
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

LEDGER_COLUMNS = ["window_id", "entity", "record_id", "outcome"]

ENTITY_ORDER = ("candidates", "placements", "agencies")

DELETED_OUTCOME = "deleted"


class LedgerStore:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write_all(self, result: Any, windows: list[Any]) -> dict[str, Any]:
        order = {w.window_id: i for i, w in enumerate(windows)}
        rows = [dict(r) for r in getattr(result, "rows", [])]
        rows.sort(key=lambda r: (
            order.get(str(r.get("window_id")), len(order)),
            ENTITY_ORDER.index(r["entity"]) if r.get("entity") in ENTITY_ORDER else len(ENTITY_ORDER),
            str(r.get("record_id")),
        ))

        path = self.output_dir / "activity_ledger.csv"
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=LEDGER_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({c: row.get(c, "") for c in LEDGER_COLUMNS})

        scanned = dict(getattr(result, "scanned", {}) or {})
        per_window: list[dict[str, Any]] = []
        for window in windows:
            bucket: dict[str, Any] = {"window_id": window.window_id}
            for entity in ENTITY_ORDER:
                placed = [r for r in rows
                          if r.get("window_id") == window.window_id and r.get("entity") == entity]
                deleted = [r for r in placed if r.get("outcome") == DELETED_OUTCOME]
                bucket[entity] = {
                    "total": len(placed),
                    "live": len(placed) - len(deleted),
                    "deleted": len(deleted),
                }
            per_window.append(bucket)

        outside: dict[str, int] = {}
        for entity in ENTITY_ORDER:
            placed_ids = {str(r.get("record_id")) for r in rows if r.get("entity") == entity}
            outside[entity] = int(scanned.get(entity, 0) or 0) - len(placed_ids)

        summary = {
            "per_window": per_window,
            "tenant": {e: int(scanned.get(e, 0) or 0) for e in ENTITY_ORDER},
            "outside_windows": outside,
        }
        (self.output_dir / "result.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return summary

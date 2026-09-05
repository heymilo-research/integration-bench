"""Output writer for the redeployment sync.

``movement_log.json`` is one entry per ROW of the redeployment export, in the order
the rows appear in that file. Shape only -- every value below is a placeholder
and does not describe a real export or tenant::

    {"row_count": 0,
     "applied_count": 0,
     "rejected_count": 0,
     "unknown_count": 0,
     "movements": [
       {"movement_ref": "RD-0000-00",
        "from_placement_id": "<vendor id>",
        "to_placement_id": "<vendor id>",
        "outcome": "applied",          # or "rejected" / "unknown"
        "from_stage": "<stage>",       # null unless the pair was applied
        "to_stage": "<stage>",         # null unless the pair was applied
        "to_fee_amount": 0,            # null unless the pair was applied
        "note_id": "<vendor id>",      # null unless a note was filed
        "source_line": 0},
       ...]}

The three ``*_count`` fields are derived from ``movements`` here, so they can
never disagree with it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUTCOMES = ("applied", "rejected", "unknown")


class ReportWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, movements: list[dict[str, Any]]) -> dict[str, Any]:
        normalised: list[dict[str, Any]] = []
        for movement in movements:
            normalised.append({
                "movement_ref": movement.get("movement_ref"),
                "from_placement_id": movement.get("from_placement_id"),
                "to_placement_id": movement.get("to_placement_id"),
                "outcome": movement.get("outcome"),
                "from_stage": movement.get("from_stage"),
                "to_stage": movement.get("to_stage"),
                "to_fee_amount": movement.get("to_fee_amount"),
                "note_id": movement.get("note_id"),
                "source_line": movement.get("source_line"),
            })
        report: dict[str, Any] = {
            "row_count": len(normalised),
            "movements": normalised,
        }
        for outcome in OUTCOMES:
            report[f"{outcome}_count"] = sum(
                1 for entry in normalised if entry.get("outcome") == outcome
            )
        (self.output_dir / "movement_log.json").write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
        )
        return report

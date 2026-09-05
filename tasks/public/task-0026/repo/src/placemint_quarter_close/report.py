"""Output writer for the quarter close.

``close_report.json`` is one entry per LINE of the placement-line export, in the
order the lines appear in that file. Shape only -- every value below is a
placeholder and does not describe a real export or tenant::

    {"line_count": 0,
     "applied_count": 0,
     "held_count": 0,
     "retired_count": 0,
     "unknown_count": 0,
     "lines": [
       {"line_ref": "L-0000",
        "invoice_ref": "INV-0000-00",
        "placement_id": "<vendor id>",
        "outcome": "applied",          # or "held" / "retired" / "unknown"
        "fee_amount": 0,               # null unless the line was applied
        "stage": "<stage>",            # null unless the line was applied
        "note_id": "<vendor id>"},     # null unless the line was applied
       ...]}

The four ``*_count`` fields are derived from ``lines`` here, so they can never
disagree with it. Nothing about ordering other than "file order" is
significant.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUTCOMES = ("applied", "held", "retired", "unknown")


class ReportWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, lines: list[dict[str, Any]]) -> dict[str, Any]:
        normalised: list[dict[str, Any]] = []
        for line in lines:
            entry = {
                "line_ref": line.get("line_ref"),
                "invoice_ref": line.get("invoice_ref"),
                "placement_id": line.get("placement_id"),
                "outcome": line.get("outcome"),
                "fee_amount": line.get("fee_amount"),
                "stage": line.get("stage"),
                "note_id": line.get("note_id"),
            }
            normalised.append(entry)
        report: dict[str, Any] = {
            "line_count": len(normalised),
            "lines": normalised,
        }
        for outcome in OUTCOMES:
            report[f"{outcome}_count"] = sum(
                1 for entry in normalised if entry.get("outcome") == outcome
            )
        (self.output_dir / "close_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
        )
        return report

"""Output writer for the corrections run.

``correction_log.json`` is one entry per CORRECTION the run handled, in the
order they came out of the export. Shape only -- every value below is a
placeholder and does not describe a real export or tenant::

    {"row_count": 0,
     "applied_count": 0,
     "rejected_count": 0,
     "unknown_count": 0,
     "corrections": [
       {"correction_ref": "C-0000",     # null for a row that would not parse
        "placement_id": "<vendor id>",  # null likewise
        "outcome": "applied",           # or "rejected" / "unknown"
        "role_title": "<role>",         # null unless applied
        "fee_amount": 0,                # null unless applied
        "note_id": "<vendor id>",       # null unless applied
        "source_line": 0},              # the export line the entry came from
       ...]}

The three ``*_count`` fields and ``row_count`` are derived from ``corrections``
here, so they can never disagree with it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUTCOMES = ("applied", "rejected", "unknown")


class CorrectionLogWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, entries: list[dict[str, Any]]) -> dict[str, Any]:
        normalised = [
            {
                "correction_ref": entry.get("correction_ref"),
                "placement_id": entry.get("placement_id"),
                "outcome": entry.get("outcome"),
                "role_title": entry.get("role_title"),
                "fee_amount": entry.get("fee_amount"),
                "note_id": entry.get("note_id"),
                "source_line": entry.get("source_line"),
            }
            for entry in entries
        ]
        log: dict[str, Any] = {"row_count": len(normalised), "corrections": normalised}
        for outcome in OUTCOMES:
            log[f"{outcome}_count"] = sum(
                1 for entry in normalised if entry.get("outcome") == outcome)
        (self.output_dir / "correction_log.json").write_text(
            json.dumps(log, indent=2, sort_keys=True), encoding="utf-8")
        return log

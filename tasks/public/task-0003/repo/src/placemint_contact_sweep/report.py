"""Output writer for the contact sweep.

``sweep_report.json`` holds one entry per placement the sweep is scoped to,
sorted by ``placement_id`` so the artifact never depends on the order the feeds
came back in. Shape only -- every value below is a placeholder and does not
describe a real feed or tenant::

    {"scope_count": 0,
     "stalled_count": 0,
     "fresh_count": 0,
     "placements": [
       {"placement_id": "<vendor id>",
        "client_id": "<vendor id>",
        "stage": "<stage>",
        "last_note_id": "<vendor id>",     # null when there is no contact
        "last_contact_at": "<iso8601>",    # null when there is no contact
        "stalled": false,
        "note_id": "<vendor id>"},         # null unless a chase-up was filed
       ...]}

``scope_count``, ``stalled_count`` and ``fresh_count`` are derived from
``placements`` here, so they can never disagree with it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ReportWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        normalised: list[dict[str, Any]] = []
        for row in rows:
            normalised.append({
                "placement_id": row.get("placement_id"),
                "client_id": row.get("client_id"),
                "stage": row.get("stage"),
                "last_note_id": row.get("last_note_id"),
                "last_contact_at": row.get("last_contact_at"),
                "stalled": bool(row.get("stalled")),
                "note_id": row.get("note_id"),
            })
        normalised.sort(key=lambda entry: str(entry.get("placement_id")))
        report: dict[str, Any] = {
            "scope_count": len(normalised),
            "stalled_count": sum(1 for entry in normalised if entry["stalled"]),
            "fresh_count": sum(1 for entry in normalised if not entry["stalled"]),
            "placements": normalised,
        }
        (self.output_dir / "sweep_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
        )
        return report

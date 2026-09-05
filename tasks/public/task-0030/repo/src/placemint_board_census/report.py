"""Output writer for the board census.

``census_report.json`` carries both directions of the census: one ``rows`` entry
per ROW of the desk board, in the order the rows appear in that file, and one
``unclaimed`` entry per Placemint placement no board row accounts for, sorted by
``placement_id`` so the artifact never depends on the order the pages came back
in. Shape only -- every value below is a placeholder and does not describe a
real board or tenant::

    {"board_row_count": 0,
     "matched_count": 0,
     "retired_count": 0,
     "unmatched_count": 0,
     "unclaimed_count": 0,
     "rows": [
       {"board_ref": "XX-0000",
        "verdict": "matched",          # or "retired" / "unmatched"
        "placement_id": "<vendor id>", # null on an unmatched row
        "source_line": 0},
       ...],
     "unclaimed": [
       {"placement_id": "<vendor id>",
        "client_id": "<vendor id>",
        "stage": "<stage>",
        "note_id": "<vendor id>"},     # null when no note was filed
       ...]}

The four ``*_count`` fields are derived from ``rows``/``unclaimed`` here, so they
can never disagree with them.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

VERDICTS = ("matched", "retired", "unmatched")


class ReportWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, rows: list[dict[str, Any]],
              unclaimed: list[dict[str, Any]]) -> dict[str, Any]:
        normalised_rows: list[dict[str, Any]] = []
        for row in rows:
            normalised_rows.append({
                "board_ref": row.get("board_ref"),
                "verdict": row.get("verdict"),
                "placement_id": row.get("placement_id"),
                "source_line": row.get("source_line"),
            })
        normalised_unclaimed: list[dict[str, Any]] = []
        for entry in unclaimed:
            normalised_unclaimed.append({
                "placement_id": entry.get("placement_id"),
                "client_id": entry.get("client_id"),
                "stage": entry.get("stage"),
                "note_id": entry.get("note_id"),
            })
        normalised_unclaimed.sort(key=lambda entry: str(entry.get("placement_id")))

        report: dict[str, Any] = {
            "board_row_count": len(normalised_rows),
            "unclaimed_count": len(normalised_unclaimed),
            "rows": normalised_rows,
            "unclaimed": normalised_unclaimed,
        }
        for verdict in VERDICTS:
            report[f"{verdict}_count"] = sum(
                1 for row in normalised_rows if row.get("verdict") == verdict
            )
        (self.output_dir / "census_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
        )
        return report

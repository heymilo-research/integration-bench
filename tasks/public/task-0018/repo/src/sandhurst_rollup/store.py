"""Output artifact writer.

Writes ``rollup.csv`` and ``result.json`` into ``OUTPUT_DIR`` in the shape
Finance's loader and the on-call dashboard expect. This layer does not
interpret or reformat any value it is handed: rows arrive in the order the
pass produced them and are written in that order. Every example value below
is a placeholder that shares no data with the tenant.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

RESULT_NAME = "result.json"
ROLLUP_NAME = "rollup.csv"
ROLLUP_COLUMNS = (
    "application_id",
    "candidate_id",
    "requisition_id",
    "stage",
    "disposition",
    "last_change_at",
)


class RollupStore:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write_result(self, summary: dict[str, Any]) -> Path:
        path = self.output_dir / RESULT_NAME
        path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
        return path

    def write_rollup(self, rows: list[dict[str, Any]]) -> Path:
        """One CSV line per rollup row, e.g.::

            application_id,candidate_id,requisition_id,stage,disposition,last_change_at
            XX-0000,YY-0000,ZZ-0000,somestage,working,0000-00-00T00:00:00Z
        """
        path = self.output_dir / ROLLUP_NAME
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(ROLLUP_COLUMNS)
            for row in rows:
                writer.writerow([
                    "" if row.get(column) is None else str(row.get(column))
                    for column in ROLLUP_COLUMNS
                ])
        return path

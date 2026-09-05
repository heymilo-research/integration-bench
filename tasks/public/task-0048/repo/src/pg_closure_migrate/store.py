"""Output artifacts.

Writes the three files the ticket's output contract names. Shapes only --
nothing here decides anything about an archive row.

Example of the row shape this module consumes (placeholder values; they share
no data with the archive or the sandbox)::

    {"ref": "XX-0000", "record_kind": "WORKER", "pg_id": "xxx_0000",
     "outcome": "<outcome>", "removed_at": None, "blocked_by": []}
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

CSV_COLUMNS = ["brackett_ref", "record_kind", "pg_id", "outcome", "removed_at", "blocked_by"]


class ClosureStore:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write_result(
        self,
        rows: list[dict[str, Any]],
        counts: dict[str, int],
        discovered: list[dict[str, Any]],
    ) -> None:
        payload = {"rows": rows, "counts": counts, "discovered": discovered}
        (self.output_dir / "result.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def write_import_report(self, rows: list[dict[str, Any]]) -> None:
        with open(self.output_dir / "import_report.csv", "w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh, lineterminator="\n")
            writer.writerow(CSV_COLUMNS)
            for row in rows:
                writer.writerow([
                    row.get("ref") or "",
                    row.get("record_kind") or "",
                    row.get("pg_id") or "",
                    row.get("outcome") or "",
                    "" if row.get("removed_at") is None else row["removed_at"],
                    ";".join(row.get("blocked_by") or []),
                ])

    def write_writeback_log(self, closed: list[dict[str, Any]]) -> None:
        (self.output_dir / "writeback_log.json").write_text(
            json.dumps(closed, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

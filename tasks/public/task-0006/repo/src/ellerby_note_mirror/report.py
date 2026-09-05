"""The two artifacts the mirror leaves behind.

    note row  {"note_id": str, "worker_id": str, "author": str, "body": str,
               "created_utc": str, "updated_utc": str,
               "state": "active"|"retired"}
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

NOTE_COLUMNS = ["note_id", "worker_id", "author", "body", "created_utc",
                "updated_utc", "state"]


class ReportWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, rows: list[dict[str, Any]],
              summary: dict[str, Any]) -> dict[str, Any]:
        path = self.output_dir / "import_report.csv"
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=NOTE_COLUMNS)
            writer.writeheader()
            for row in rows:
                writer.writerow({column: row.get(column, "") for column in NOTE_COLUMNS})

        (self.output_dir / "result.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return summary

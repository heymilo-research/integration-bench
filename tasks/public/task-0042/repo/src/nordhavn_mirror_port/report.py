"""The two artifacts this job leaves behind.

Today they carry the mirror's LEGACY storage shape, one row per mirrored
record:

    mirror row  {"mirror_row_id": str, "entity": str, "record_id": str,
                 "stored_zone": str, "stored_local": str}

``result.json`` carries the row count and the offset table the run worked to.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

MIRROR_COLUMNS = [
    "mirror_row_id", "entity", "record_id", "stored_zone", "stored_local"]


class ReportWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, rows: list[dict[str, Any]],
              summary: dict[str, Any]) -> dict[str, Any]:
        path = self.output_dir / "import_report.csv"
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=MIRROR_COLUMNS)
            writer.writeheader()
            for row in rows:
                writer.writerow({column: row.get(column, "") for column in MIRROR_COLUMNS})

        (self.output_dir / "result.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return summary

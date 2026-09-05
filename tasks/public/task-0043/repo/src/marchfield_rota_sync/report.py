"""The two warehouse artifacts.

Both are rewritten from the durable ledger on every pass, so they always show
everything the warehouse has been handed to date.

    ledger row  {"run": int, "entity": str, "record_id": str,
                 "change": "upsert"|"delete", "updated_at_utc": str}
    pass entry  {"run": int, "watermark_in": str, "watermark_out": str,
                 "emitted": [record_id, ...], "removed": [record_id, ...],
                 "upserts": int, "deletes": int}
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

LEDGER_COLUMNS = ["run", "entity", "record_id", "change", "updated_at_utc"]


class ReportWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, ledger: list[dict[str, Any]], runs: list[dict[str, Any]]) -> dict[str, Any]:
        path = self.output_dir / "import_report.csv"
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=LEDGER_COLUMNS)
            writer.writeheader()
            for row in ledger:
                writer.writerow({column: row[column] for column in LEDGER_COLUMNS})

        payload = {
            "run_count": len(runs),
            "ledger_row_count": len(ledger),
            "distinct_record_count": len(
                {(row["entity"], row["record_id"]) for row in ledger}),
            "runs": runs,
        }
        (self.output_dir / "result.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return payload

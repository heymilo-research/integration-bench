from __future__ import annotations

import csv
import json
from pathlib import Path

from globalhire_mobility.reconcile import CaseResult


FIELDS = [
    "case_ref",
    "source_line",
    "duplicate_count",
    "candidate_id",
    "placement_id",
    "agency_id",
    "requested_stage",
    "current_stage",
    "outcome",
    "reason",
]


def write_report(output_dir: Path, source_rows: int, cases: list[CaseResult]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = [case.to_dict() for case in cases]
    document = {
        "status": "complete",
        "source_rows": source_rows,
        "case_count": len(rows),
        "updated_count": sum(row["outcome"] == "updated" for row in rows),
        "unchanged_count": sum(row["outcome"] == "unchanged" for row in rows),
        "rejected_count": sum(row["outcome"] == "rejected" for row in rows),
        "cases": rows,
    }
    (output_dir / "reconciliation.json").write_text(
        json.dumps(document, indent=2) + "\n", encoding="utf-8"
    )
    with (output_dir / "reconciliation.csv").open(
        "w", encoding="utf-8", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

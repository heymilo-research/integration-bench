"""Output writer for the book extract.

``book_extract.json`` holds one entry per placement the extract covers, sorted
by ``placement_id`` so the artifact never depends on the order the pages came
back in. Shape only -- every value below is a placeholder and none of them
describes any row of any real book, snapshot or tenant::

    {"placement_count": 0,
     "billable_count": 0,
     "on_hold_count": 0,
     "fee_total_billable": 0,
     "placements": [
       {"placement_id": "<vendor id>",
        "client_id": "<vendor id>",
        "client_name": "<account name>",
        "client_industry": "<industry>",
        "candidate_name": "<name>",
        "role_title": "<role>",
        "stage": "<stage>",
        "fee_amount": 0,               # null when the placement carries no fee
        "billable": true},
       ...]}

The three counts and ``fee_total_billable`` are derived from ``placements``
here, so they can never disagree with it. ``fee_total_billable`` sums the fee of
every billable row that carries one, rounded to two decimals.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIELDS = ("placement_id", "client_id", "client_name", "client_industry",
          "candidate_name", "role_title", "stage", "fee_amount", "billable")


class ReportWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        normalised: list[dict[str, Any]] = []
        for row in rows:
            entry = {field: row.get(field) for field in FIELDS}
            entry["billable"] = bool(row.get("billable"))
            normalised.append(entry)
        normalised.sort(key=lambda entry: str(entry.get("placement_id")))

        billable = [entry for entry in normalised if entry["billable"]]
        fee_total = round(sum(
            float(entry["fee_amount"]) for entry in billable
            if isinstance(entry["fee_amount"], (int, float))), 2)
        report: dict[str, Any] = {
            "placement_count": len(normalised),
            "billable_count": len(billable),
            "on_hold_count": len(normalised) - len(billable),
            "fee_total_billable": fee_total,
            "placements": normalised,
        }
        (self.output_dir / "book_extract.json").write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
        )
        return report

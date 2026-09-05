"""Cycle artifacts.

``result.json`` is the change file the cutover produces. ``changes`` carries one
entry per Vettly record the cycle is reporting, in any order::

    {"cursor_used": 0,
     "next_cursor": 0,
     "record_count": 0,
     "counts": {"subject": 0, "check": 0, "report": 0,
                "upsert": 0, "retire": 0},
     "retired_ids": [],
     "changes": [{"record_id": "<id>",
                  "kind": "subject",
                  "op": "upsert",
                  "subject_id": "<id>",
                  "subject_email": "someone@example.invalid",
                  "updated_at": 0,
                  "detail": "<value>"}]}

``import_report.csv`` is the warehouse loader's instruction file: header plus
one row per entry in ``changes``, columns
``record_id,kind,op,subject_id,subject_email``. Row order is not significant in
either file.

``counts``, ``record_count`` and ``retired_ids`` are derived here from the
entries handed in, so the cycle only has to get each entry right once.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

KINDS = ["subject", "check", "report"]
OPS = ["upsert", "retire"]
_ENTRY_FIELDS = [
    "record_id", "kind", "op", "subject_id", "subject_email", "updated_at",
    "detail",
]
_CSV_FIELDS = ["record_id", "kind", "op", "subject_id", "subject_email"]


class FeedStore:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        changes: list[dict[str, Any]],
        *,
        cursor_used: Any,
        next_cursor: Any,
    ) -> dict[str, Any]:
        ordered = sorted(changes, key=lambda row: str(row.get("record_id")))
        normalised = [
            {field: row.get(field) for field in _ENTRY_FIELDS} for row in ordered
        ]

        counts: dict[str, int] = {key: 0 for key in KINDS + OPS}
        retired: list[str] = []
        for row in normalised:
            kind = str(row.get("kind") or "")
            op = str(row.get("op") or "")
            if kind in counts:
                counts[kind] += 1
            if op in counts:
                counts[op] += 1
            if op == "retire":
                retired.append(str(row.get("record_id")))

        result = {
            "cursor_used": cursor_used,
            "next_cursor": next_cursor,
            "record_count": len(normalised),
            "counts": counts,
            "retired_ids": sorted(retired),
            "changes": normalised,
        }
        (self.output_dir / "result.json").write_text(
            json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")

        with (self.output_dir / "import_report.csv").open(
            "w", newline="", encoding="utf-8"
        ) as fh:
            writer = csv.DictWriter(
                fh, fieldnames=_CSV_FIELDS, extrasaction="ignore")
            writer.writeheader()
            for row in normalised:
                writer.writerow({
                    field: ("" if row.get(field) is None else row.get(field))
                    for field in _CSV_FIELDS
                })
        return result

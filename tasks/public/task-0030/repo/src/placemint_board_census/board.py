"""The desk board export.

One row per line of ``input/desk_board.csv``, values stripped, in file order,
with the physical line number attached so a report entry can be traced back.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


def read_board(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for line_no, raw in enumerate(csv.DictReader(fh), start=2):
            row = {key: (value or "").strip() for key, value in raw.items()}
            row["source_line"] = line_no
            rows.append(row)
    return rows

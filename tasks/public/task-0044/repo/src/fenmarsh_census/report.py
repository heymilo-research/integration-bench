"""Output writers for the nightly census.

Two artifacts. Shape only -- every value below is a placeholder and none of
them describes any row of any real roster or tenant.

``roster_census.csv`` is one line per crew member the census counted, sorted by
``worker_id``::

    worker_id,role,status,standing
    wkr_0000,<role>,<status>,<standing>

``standing`` is ``active`` or ``removed``.

``census_summary.json``::

    {"roster_rows": 0,
     "active_headcount": 0,
     "removed_headcount": 0,
     "by_role":   [{"role": "<role>", "active": 0, "removed": 0}, ...],
     "by_status": [{"status": "<status>", "headcount": 0}, ...],
     "pages_read": 0}

``by_role`` is written sorted by ``role`` and covers every role the census saw.
``by_status`` counts ACTIVE crew only -- the capacity model does not schedule
somebody who has left -- and is written sorted by ``status``. Nothing else
about ordering is significant.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

CENSUS_COLUMNS = ("worker_id", "role", "status", "standing")


class ReportWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, lines: list[dict[str, Any]], summary: dict[str, Any]) -> None:
        with (self.output_dir / "roster_census.csv").open(
            "w", encoding="utf-8", newline=""
        ) as fh:
            writer = csv.DictWriter(fh, fieldnames=list(CENSUS_COLUMNS))
            writer.writeheader()
            for line in sorted(lines, key=lambda l: str(l.get("worker_id"))):
                writer.writerow({c: line.get(c, "") or "" for c in CENSUS_COLUMNS})

        (self.output_dir / "census_summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
        )

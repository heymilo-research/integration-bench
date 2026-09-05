"""Output writers for the weekly top-up.

Two artifacts. Shape only -- every value below is a placeholder and none of
them describes any row of any real placement file, roster or tenant.

``placement_report.csv`` is one row per row of the agency's file, in the order
the file holds them::

    placement_ref,shift_date,crew_email,worker_id,outcome
    XX-0000,0000-00-00,someone@example.invalid,wkr_0000,<outcome>

``outcome`` is ``matched`` for a crew member CrewCall already held and
``created`` for one this run signed up.

``topup_summary.json`` is one entry per crew member the file places::

    {"row_count": 0,
     "person_count": 0,
     "matched_count": 0,
     "created_count": 0,
     "roster_rows_seen": 0,
     "people": [
       {"person_key": "someone@example.invalid",
        "crew_email": "someone@example.invalid",
        "crew_name": "<name>",
        "placement_refs": ["XX-0000", "..."],
        "outcome": "matched",
        "worker_id": "wkr_0000"},
       ...]}

``roster_rows_seen`` is how many distinct worker records the sweep ended up
holding. ``placement_refs`` is written sorted; ``people`` is written sorted by
``person_key``. Nothing else about ordering is significant.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

PLACEMENT_COLUMNS = ("placement_ref", "shift_date", "crew_email", "worker_id", "outcome")


class ReportWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, placement_rows: list[dict[str, Any]],
              people: list[dict[str, Any]],
              *, roster_rows_seen: int) -> dict[str, Any]:
        with (self.output_dir / "placement_report.csv").open(
            "w", encoding="utf-8", newline=""
        ) as fh:
            writer = csv.DictWriter(fh, fieldnames=list(PLACEMENT_COLUMNS))
            writer.writeheader()
            for row in placement_rows:
                writer.writerow({c: row.get(c, "") or "" for c in PLACEMENT_COLUMNS})

        normalised = []
        for person in people:
            entry = {k: v for k, v in person.items() if k != "crew_phone"}
            entry["placement_refs"] = sorted(entry.get("placement_refs") or [])
            normalised.append(entry)

        payload = {
            "row_count": len(placement_rows),
            "person_count": len(normalised),
            "matched_count": sum(1 for p in normalised if p.get("outcome") == "matched"),
            "created_count": sum(1 for p in normalised if p.get("outcome") == "created"),
            "roster_rows_seen": int(roster_rows_seen),
            "people": sorted(normalised, key=lambda p: str(p.get("person_key"))),
        }
        (self.output_dir / "topup_summary.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
        return payload

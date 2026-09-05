"""Output writer for the signup import.

``dedupe_report.json`` is one entry per PERSON, not per file row. Shape only --
every value below is a placeholder and does not describe a real export or
tenant::

    {"row_count": 0,
     "person_count": 0,
     "created_count": 0,
     "skipped_count": 0,
     "people": [
       {"person_key": "<identity>",           # the tenant's identity key
        "survivor_submission_id": "<id>",     # the row the canonical values came from
        "submission_ids": ["<id>", "..."],    # every row in the group
        "first_name": "<given name>",
        "last_name": "<family name>",
        "email": "someone@example.invalid",
        "role": "<role>",
        "outcome": "created",                 # or "skipped"
        "worker_id": "<vendor id>"},          # the CrewCall id this person is
       ...]}

``submission_ids`` is written sorted; ``people`` is written sorted by
``person_key``. Nothing else about ordering is significant.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class ReportWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, row_count: int, people: list[dict[str, Any]]) -> dict[str, Any]:
        normalised = []
        for person in people:
            entry = dict(person)
            entry["submission_ids"] = sorted(entry.get("submission_ids") or [])
            normalised.append(entry)
        report = {
            "row_count": row_count,
            "person_count": len(normalised),
            "created_count": sum(1 for p in normalised if p.get("outcome") == "created"),
            "skipped_count": sum(1 for p in normalised if p.get("outcome") == "skipped"),
            "people": sorted(normalised, key=lambda p: str(p.get("person_key"))),
        }
        (self.output_dir / "dedupe_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
        )
        return report

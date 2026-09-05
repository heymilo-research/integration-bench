"""Output artifact writer.

Shapes only. Every example value below is a placeholder that shares no data
with the extract, the roster or the tenant.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

REPORT_FIELDS = ["tag_id", "name", "status", "member_count"]


def _cell(value: Any) -> str:
    """CSV rendering: absence is the empty string."""
    return "" if value is None else str(value)


class IndexStore:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write_index(
        self,
        skills: list[dict[str, Any]],
        people: list[dict[str, Any]],
        counts: dict[str, int],
    ) -> None:
        """``{"skills": [...], "people": [...], "counts": {...}}``

        ``skills`` entries look like::

            {"tag_id": "yyy_0000", "name": "<skill>", "status": "live",
             "members": ["xxx_0000"], "member_count": 0}

        ``people`` entries look like::

            {"sw_id": "xxx_0000", "nm": "<display name>",
             "skills": ["yyy_0000"], "skill_count": 0}
        """
        doc = {
            "skills": sorted(skills, key=lambda r: str(r.get("tag_id"))),
            "people": sorted(people, key=lambda r: str(r.get("sw_id"))),
            "counts": counts,
        }
        (self.output_dir / "capability_index.json").write_text(
            json.dumps(doc, indent=2, sort_keys=True), encoding="utf-8"
        )

    def write_skills_report(self, skills: list[dict[str, Any]]) -> None:
        out = [{k: _cell(row.get(k)) for k in REPORT_FIELDS} for row in skills]
        out.sort(key=lambda r: r["tag_id"])
        path = self.output_dir / "skills_report.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=REPORT_FIELDS)
            writer.writeheader()
            writer.writerows(out)

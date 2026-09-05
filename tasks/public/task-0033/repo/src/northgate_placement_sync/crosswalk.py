"""Ken's crosswalk file.

One row per requisition Revenue Ops bill for. Rows are returned in file order,
which is the order the report has to be written in.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Link:
    application_id: str
    placement_id: str
    owner: str


def read_links(path: Path) -> list[Link]:
    with Path(path).open(newline="", encoding="utf-8") as fh:
        return [
            Link(
                application_id=(row.get("application_id") or "").strip(),
                placement_id=(row.get("placement_id") or "").strip(),
                owner=(row.get("owner") or "").strip(),
            )
            for row in csv.DictReader(fh)
            if (row.get("application_id") or "").strip()
        ]

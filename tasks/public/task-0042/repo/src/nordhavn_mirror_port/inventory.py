"""The two input files.

``crew_roster.csv``      the worker ids Nordhavn owns on the shared tenant.
                         columns: worker_id

``mirror_inventory.csv``  a dump of every row the Postgres mirror holds today,
                          in the mirror's own storage shape.
                          columns: mirror_row_id, entity, record_id,
                                   stored_zone, stored_local
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

INVENTORY_COLUMNS = [
    "mirror_row_id", "entity", "record_id", "stored_zone", "stored_local"]


def read_crew(path: Path) -> list[str]:
    with Path(path).open(encoding="utf-8", newline="") as fh:
        return [str(row["worker_id"]).strip()
                for row in csv.DictReader(fh) if row.get("worker_id")]


def read_inventory(path: Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]

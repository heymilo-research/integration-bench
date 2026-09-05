"""Environment-driven configuration.

- ``VENDOR_BASE_URL``  base URL of the Rosterly sandbox.
- ``RY_CLIENT_ID`` / ``RY_CLIENT_SECRET``  the OAuth client credentials.
- ``CREW_ROSTER_FILE``  the crew Nordhavn owns, one worker id per line.
- ``MIRROR_INVENTORY_FILE``  the dump of what the Postgres mirror holds today.
- ``OUTPUT_DIR``  where the run's artifacts land.
- ``PAGE_LIMIT``  page size to ask list endpoints for.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    vendor_base_url: str
    client_id: str
    client_secret: str
    crew_roster_file: Path
    mirror_inventory_file: Path
    output_dir: Path
    page_limit: int

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            vendor_base_url=os.environ.get(
                "VENDOR_BASE_URL", "http://localhost:8000").rstrip("/"),
            client_id=os.environ.get("RY_CLIENT_ID", ""),
            client_secret=os.environ.get("RY_CLIENT_SECRET", ""),
            crew_roster_file=Path(os.environ.get(
                "CREW_ROSTER_FILE", "./input/crew_roster.csv")),
            mirror_inventory_file=Path(os.environ.get(
                "MIRROR_INVENTORY_FILE", "./input/mirror_inventory.csv")),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "./output")),
            page_limit=int(os.environ.get("PAGE_LIMIT", "50")),
        )

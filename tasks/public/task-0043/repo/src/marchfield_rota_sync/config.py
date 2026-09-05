"""Environment-driven configuration.

- ``VENDOR_BASE_URL``   base URL of the Rosterly sandbox.
- ``RY_CLIENT_ID`` / ``RY_CLIENT_SECRET``  the OAuth client credentials.
- ``SYNC_SINCE``       the instant the warehouse was loaded from Rosterly's
                       dump; the first pass starts from here.
- ``STATE_DIR``        where the pass-to-pass state lives.
- ``OUTPUT_DIR``       where the run's artifacts land.
- ``PAGE_LIMIT``       page size to ask list endpoints for.
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
    sync_since: str
    state_dir: Path
    output_dir: Path
    page_limit: int

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            vendor_base_url=os.environ.get(
                "VENDOR_BASE_URL", "http://localhost:8000").rstrip("/"),
            client_id=os.environ.get("RY_CLIENT_ID", ""),
            client_secret=os.environ.get("RY_CLIENT_SECRET", ""),
            sync_since=os.environ.get("SYNC_SINCE", "").strip(),
            state_dir=Path(os.environ.get("STATE_DIR", "./state")),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "./output")),
            page_limit=int(os.environ.get("PAGE_LIMIT", "50")),
        )

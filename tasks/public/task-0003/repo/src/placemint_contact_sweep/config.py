"""Environment-driven configuration.

- ``VENDOR_BASE_URL``   base URL of the Placemint sandbox.
- ``PM_CLIENT_ID``      OAuth client-credentials id.
- ``PM_CLIENT_SECRET``  OAuth client-credentials secret.
- ``STALE_BEFORE``      the review horizon, an ISO 8601 Z instant.
- ``OUTPUT_DIR``        where the run's artifact lands.
- ``PAGE_LIMIT``        page size to ask list endpoints for.
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
    stale_before: str
    output_dir: Path
    page_limit: int

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            vendor_base_url=os.environ.get(
                "VENDOR_BASE_URL", "http://localhost:8000"
            ).rstrip("/"),
            client_id=os.environ.get("PM_CLIENT_ID", ""),
            client_secret=os.environ.get("PM_CLIENT_SECRET", ""),
            stale_before=os.environ.get("STALE_BEFORE", ""),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "./output")),
            page_limit=int(os.environ.get("PAGE_LIMIT", "100")),
        )

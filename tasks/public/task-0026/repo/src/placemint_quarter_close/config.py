"""Environment-driven configuration.

- ``VENDOR_BASE_URL``   base URL of the Placemint sandbox.
- ``PM_CLIENT_ID``      OAuth client-credentials id.
- ``PM_CLIENT_SECRET``  OAuth client-credentials secret.
- ``INVOICES_FILE``     the billing tool's invoice header export.
- ``LINES_FILE``        the billing tool's placement-line export.
- ``OUTPUT_DIR``        where the run's artifact lands.
- ``PAGE_LIMIT``        page size to ask list endpoints for.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
DEFAULT_INVOICES = _REPO / "input" / "invoices.csv"
DEFAULT_LINES = _REPO / "input" / "placement_lines.csv"


@dataclass(frozen=True)
class Config:
    vendor_base_url: str
    client_id: str
    client_secret: str
    invoices_file: Path
    lines_file: Path
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
            invoices_file=Path(os.environ.get("INVOICES_FILE") or DEFAULT_INVOICES),
            lines_file=Path(os.environ.get("LINES_FILE") or DEFAULT_LINES),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "./output")),
            page_limit=int(os.environ.get("PAGE_LIMIT", "100")),
        )

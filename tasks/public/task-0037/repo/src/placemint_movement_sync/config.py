"""Environment-driven configuration.

- ``VENDOR_BASE_URL``   base URL of the Placemint sandbox.
- ``PM_CLIENT_ID``      OAuth client-credentials id.
- ``PM_CLIENT_SECRET``  OAuth client-credentials secret.
- ``REDEPLOYMENTS_FILE``    the ATS's daily redeployment export.
- ``OUTPUT_DIR``        where the run's artifact lands.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
DEFAULT_REDEPLOYMENTS = _REPO / "input" / "redeployments.csv"


@dataclass(frozen=True)
class Config:
    vendor_base_url: str
    client_id: str
    client_secret: str
    redeployments_file: Path
    output_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            vendor_base_url=os.environ.get(
                "VENDOR_BASE_URL", "http://localhost:8000"
            ).rstrip("/"),
            client_id=os.environ.get("PM_CLIENT_ID", ""),
            client_secret=os.environ.get("PM_CLIENT_SECRET", ""),
            redeployments_file=Path(os.environ.get("REDEPLOYMENTS_FILE") or DEFAULT_REDEPLOYMENTS),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "./output")),
        )

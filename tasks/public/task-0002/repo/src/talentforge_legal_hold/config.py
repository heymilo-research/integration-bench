"""Environment-driven configuration.

- ``VENDOR_BASE_URL``   base URL of the TalentForge sandbox.
- ``TF_CLIENT_ID`` / ``TF_CLIENT_SECRET``  this tenant's OAuth client.
- ``ROSTER_PATH``      the legal-hold roster CSV (defaults to ``input/`` in the repo).
- ``OUTPUT_DIR``       where the export lands.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROSTER = REPO_ROOT / "input" / "legal_hold_roster.csv"


@dataclass(frozen=True)
class Config:
    vendor_base_url: str
    client_id: str
    client_secret: str
    roster_path: Path
    output_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            vendor_base_url=os.environ.get(
                "VENDOR_BASE_URL", "http://localhost:8000"
            ).rstrip("/"),
            client_id=os.environ.get("TF_CLIENT_ID", ""),
            client_secret=os.environ.get("TF_CLIENT_SECRET", ""),
            roster_path=Path(os.environ.get("ROSTER_PATH", str(DEFAULT_ROSTER))),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "./output")),
        )

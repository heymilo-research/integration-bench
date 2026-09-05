"""Environment-driven configuration.

- ``VENDOR_BASE_URL``   base URL of the TalentLoop sandbox.
- ``TL_CLIENT_ID``      OAuth client-credentials id.
- ``TL_CLIENT_SECRET``  OAuth client-credentials secret.
- ``INPUT_FILE``        the scorecard export to deliver.
- ``OUTPUT_DIR``        where the run's artifacts and ledger are written.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_INPUT = Path(__file__).resolve().parents[2] / "input" / "scorecards.json"


@dataclass(frozen=True)
class Config:
    vendor_base_url: str
    client_id: str
    client_secret: str
    input_file: Path
    output_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            vendor_base_url=os.environ.get("VENDOR_BASE_URL", "http://localhost:8000").rstrip("/"),
            client_id=os.environ.get("TL_CLIENT_ID", ""),
            client_secret=os.environ.get("TL_CLIENT_SECRET", ""),
            input_file=Path(os.environ.get("INPUT_FILE") or DEFAULT_INPUT),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "./output")),
        )

"""Environment-driven configuration.

- ``VENDOR_BASE_URL``   base URL of the Vettly sandbox.
- ``VT_CLIENT_ID`` / ``VT_CLIENT_SECRET``   Vettly OAuth client credentials.
  These used to live inside vaultd; the connector holds them itself now.
- ``INPUT_DIR``         where vaultd's handover state file was dropped.
- ``OUTPUT_DIR``        where the cycle's artifacts are written.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    vettly_base_url: str
    vt_client_id: str
    vt_client_secret: str
    input_dir: Path
    output_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            vettly_base_url=os.environ.get(
                "VENDOR_BASE_URL", "http://localhost:8000").rstrip("/"),
            vt_client_id=os.environ.get("VT_CLIENT_ID", ""),
            vt_client_secret=os.environ.get("VT_CLIENT_SECRET", ""),
            input_dir=Path(os.environ.get("INPUT_DIR", "./input")),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "./output")),
        )

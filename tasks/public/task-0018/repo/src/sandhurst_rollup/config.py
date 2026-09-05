"""Environment-driven configuration.

- ``VENDOR_BASE_URL``  base URL of the RecruitOS sandbox.
- ``RO_CLIENT_ID``     OAuth client-credentials client id.
- ``RO_CLIENT_SECRET`` OAuth client-credentials client secret.
- ``MART_DROP_DIR``    directory the Reporting Mart dropped its nightly file
                       into.
- ``OUTPUT_DIR``       where this pass writes ``rollup.csv`` and
                       ``result.json``.
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
    mart_drop_dir: Path
    output_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            vendor_base_url=os.environ.get("VENDOR_BASE_URL", "http://localhost:8000").rstrip("/"),
            client_id=os.environ.get("RO_CLIENT_ID", ""),
            client_secret=os.environ.get("RO_CLIENT_SECRET", ""),
            mart_drop_dir=Path(os.environ.get("MART_DROP_DIR", "./input/mart")),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "./output")),
        )

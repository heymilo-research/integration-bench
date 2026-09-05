"""Environment-driven configuration.

- ``VENDOR_BASE_URL``  base URL of the Paygrade sandbox.
- ``PG_APP_TOKEN``     the account credential the platform team provisioned.
- ``OUTPUT_DIR``       where the cutover artifacts are written.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    vendor_base_url: str
    app_token: str
    output_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            vendor_base_url=os.environ.get("VENDOR_BASE_URL", "http://localhost:8000").rstrip("/"),
            app_token=os.environ.get("PG_APP_TOKEN", ""),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "./output")),
        )

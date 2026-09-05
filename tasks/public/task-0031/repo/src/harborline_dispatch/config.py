"""Environment-driven configuration.

- ``VENDOR_BASE_URL``  base URL of the CrewCall sandbox.
- ``CC_API_KEY``       the static key sent on every data-plane request.
- ``OUTPUT_DIR``       where the run's artifacts land.
- ``PAGE_LIMIT``       page size to ask the list endpoints for.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    vendor_base_url: str
    api_key: str
    output_dir: Path
    page_limit: int

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            vendor_base_url=os.environ.get(
                "VENDOR_BASE_URL", "http://localhost:8000"
            ).rstrip("/"),
            api_key=os.environ.get("CC_API_KEY", ""),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "./output")),
            page_limit=int(os.environ.get("PAGE_LIMIT", "25")),
        )

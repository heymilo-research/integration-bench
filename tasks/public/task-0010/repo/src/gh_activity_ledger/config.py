"""Environment-driven configuration.

- ``VENDOR_BASE_URL``  base URL of the GlobalHire sandbox.
- ``GH_API_KEY``       the static key sent on every data-plane request.
- ``OUTPUT_DIR``       directory the two artifacts are written to.
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

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            vendor_base_url=os.environ.get("VENDOR_BASE_URL", "http://localhost:8000").rstrip("/"),
            api_key=os.environ.get("GH_API_KEY", ""),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "./output")),
        )

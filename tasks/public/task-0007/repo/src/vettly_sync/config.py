"""Environment config. See ``PROBLEM.md``."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    vendor_base_url: str
    database_url: str
    client_id: str
    client_secret: str
    output_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            vendor_base_url=os.environ.get("VENDOR_BASE_URL", "http://localhost:8000").rstrip("/"),
            database_url=os.environ.get(
                "DATABASE_URL", "sqlite:////data/canonical_db"
            ),
            client_id=os.environ.get("VT_CLIENT_ID", ""),
            client_secret=os.environ.get("VT_CLIENT_SECRET", ""),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "./output")),
        )

"""Environment config. See ``PROBLEM.md``."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    vendor_base_url: str
    database_url: str
    api_key: str
    output_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            vendor_base_url=os.environ.get("VENDOR_BASE_URL", "http://localhost:8000").rstrip("/"),
            database_url=os.environ.get(
                "DATABASE_URL", "sqlite:////data/canonical_db"
            ),
            api_key=os.environ.get("GH_API_KEY", ""),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "./output")),
        )

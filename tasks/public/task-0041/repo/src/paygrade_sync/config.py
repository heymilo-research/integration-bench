"""Environment config. See ``PROBLEM.md``."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    vendor_base_url: str
    api_token: str
    input_file: Path
    output_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            vendor_base_url=os.environ.get(
                "VENDOR_BASE_URL", "http://localhost:8000"
            ).rstrip("/"),
            api_token=os.environ.get("PG_APP_TOKEN", ""),
            input_file=Path(os.environ.get("INPUT_FILE", "./input/pending_writes.json")),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "./output")),
        )

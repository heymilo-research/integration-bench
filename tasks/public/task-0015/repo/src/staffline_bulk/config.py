"""Environment config. See ``PROBLEM.md``."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    """Resolved runtime configuration."""

    vendor_base_url: str
    app_token: str
    hmac_secret: str
    database_url: str
    input_file: Path
    output_dir: Path

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        source = env if env is not None else dict(os.environ)
        return cls(
            vendor_base_url=source.get("VENDOR_BASE_URL", "http://localhost:8000").rstrip("/"),
            app_token=source.get("SL_APP_TOKEN", ""),
            hmac_secret=source.get("SL_HMAC_SECRET", ""),
            database_url=source.get(
                "DATABASE_URL", "sqlite:////data/canonical_db"
            ),
            input_file=Path(source.get("INPUT_FILE", "./input/candidate_batch.json")),
            output_dir=Path(source.get("OUTPUT_DIR", "./output")),
        )

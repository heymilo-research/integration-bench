"""Runtime configuration from environment (COMPLETE). See PROBLEM.md."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    vendor_base_url: str
    sl_app_token: str
    sl_hmac_secret: str
    database_url: str
    output_dir: Path

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        source = env if env is not None else dict(os.environ)
        return cls(
            vendor_base_url=source.get("VENDOR_BASE_URL", "http://localhost:8000").rstrip("/"),
            sl_app_token=source.get("SL_APP_TOKEN", ""),
            sl_hmac_secret=source.get("SL_HMAC_SECRET", ""),
            database_url=source.get(
                "DATABASE_URL", "sqlite:////data/canonical_db"
            ),
            output_dir=Path(source.get("OUTPUT_DIR", "./output")),
        )

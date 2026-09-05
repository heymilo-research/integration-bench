"""Environment config. See ``PROBLEM.md``."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    staffline_base_url: str
    sl_app_token: str
    sl_hmac_secret: str

    bullpen_base_url: str
    bp_client_id: str
    bp_client_secret: str

    output_dir: Path

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        source = env if env is not None else dict(os.environ)
        return cls(
            staffline_base_url=source.get("STAFFLINE_BASE_URL", "http://localhost:8001").rstrip("/"),
            sl_app_token=source.get("SL_APP_TOKEN", ""),
            sl_hmac_secret=source.get("SL_HMAC_SECRET", ""),
            bullpen_base_url=source.get("BULLPEN_BASE_URL", "http://localhost:8002").rstrip("/"),
            bp_client_id=source.get("BP_CLIENT_ID", ""),
            bp_client_secret=source.get("BP_CLIENT_SECRET", ""),
            output_dir=Path(source.get("OUTPUT_DIR", "./output")),
        )

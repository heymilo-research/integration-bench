"""Environment config. See ``PROBLEM.md``."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    """Resolved runtime configuration."""

    vendor_base_url: str
    client_id: str
    client_secret: str
    output_dir: Path

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        source = env if env is not None else dict(os.environ)
        return cls(
            vendor_base_url=source.get("VENDOR_BASE_URL", "http://localhost:8000").rstrip("/"),
            client_id=source.get("VT_CLIENT_ID", ""),
            client_secret=source.get("VT_CLIENT_SECRET", ""),
            output_dir=Path(source.get("OUTPUT_DIR", "./output")),
        )

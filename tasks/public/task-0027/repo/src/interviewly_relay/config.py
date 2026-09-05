"""Environment-driven configuration. See ``PROBLEM.md`` for variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    vendor_base_url: str
    client_id: str
    client_secret: str
    webhook_secret: str
    serve_host: str
    serve_port: int
    output_dir: Path

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "Config":
        source = env if env is not None else dict(os.environ)
        return cls(
            vendor_base_url=source.get("VENDOR_BASE_URL", "http://localhost:8000").rstrip("/"),
            client_id=source.get("IV_CLIENT_ID", ""),
            client_secret=source.get("IV_CLIENT_SECRET", ""),
            webhook_secret=source.get("IV_WEBHOOK_SECRET", ""),
            serve_host=source.get("SERVE_HOST", "0.0.0.0"),
            serve_port=int(source.get("SERVE_PORT", "4000")),
            output_dir=Path(source.get("OUTPUT_DIR", "./output")),
        )

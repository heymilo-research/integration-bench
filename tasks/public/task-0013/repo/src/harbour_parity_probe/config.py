"""Environment -> Config."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    base_url: str
    client_id: str
    client_secret: str
    webhook_secret: str
    snapshot_file: Path
    output_dir: Path
    state_dir: Path
    serve_host: str
    serve_port: int

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            base_url=os.environ.get("VENDOR_BASE_URL", "http://recruitos:8000"),
            client_id=os.environ.get("RO_CLIENT_ID", ""),
            client_secret=os.environ.get("RO_CLIENT_SECRET", ""),
            webhook_secret=os.environ.get("RO_WEBHOOK_SECRET", ""),
            snapshot_file=Path(
                os.environ.get("SNAPSHOT_FILE", "/app/input/warehouse_mirror.json")
            ),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "./output")),
            state_dir=Path(os.environ.get("STATE_DIR", "./state")),
            serve_host=os.environ.get("SERVE_HOST", "0.0.0.0"),
            serve_port=int(os.environ.get("SERVE_PORT", "4000")),
        )

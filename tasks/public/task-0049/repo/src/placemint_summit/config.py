"""Environment config. See ``PROBLEM.md``."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_DEFAULT_PENDING_WRITES = Path(__file__).resolve().parents[2] / "input" / "pending_writes.json"
_DEFAULT_STATE_PATH = Path(__file__).resolve().parents[2] / "state" / "store.json"


@dataclass(frozen=True)
class Config:
    vendor_base_url: str
    client_id: str
    client_secret: str
    webhook_secret: str
    serve_host: str
    serve_port: int
    output_dir: Path
    state_path: Path
    pending_writes_path: Path
    poll_interval_s: float

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            vendor_base_url=os.environ.get("VENDOR_BASE_URL", "http://localhost:8000").rstrip("/"),
            client_id=os.environ.get("PM_CLIENT_ID", ""),
            client_secret=os.environ.get("PM_CLIENT_SECRET", ""),
            webhook_secret=os.environ.get("PM_WEBHOOK_SECRET", "pm-test-webhook-secret"),
            serve_host=os.environ.get("SERVE_HOST", "0.0.0.0"),
            serve_port=int(os.environ.get("SERVE_PORT", "4000")),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "./output")),
            state_path=Path(os.environ.get("STATE_PATH", str(_DEFAULT_STATE_PATH))),
            pending_writes_path=Path(
                os.environ.get("PENDING_WRITES_PATH", str(_DEFAULT_PENDING_WRITES))
            ),
            poll_interval_s=float(os.environ.get("POLL_INTERVAL_S", "3")),
        )

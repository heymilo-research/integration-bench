"""Environment-driven configuration.

- ``VENDOR_BASE_URL``  base URL of the TalentForge sandbox. This is the
                        PRE-LOGIN host only; every data-plane request after
                        login goes to the session's own ``rest_url``
                        (docs/auth.md).
- ``TF_CLIENT_ID``      OAuth authorization-code client id.
- ``TF_CLIENT_SECRET``  OAuth authorization-code client secret.
- ``TF_WEBHOOK_SECRET`` shared secret TalentForge signs its deliveries with.
- ``OUTPUT_DIR``        where the mirror, the ledger and our own working state
                        live. It is mounted, so it survives the one-shot
                        container exits between passes.
- ``SERVE_HOST`` / ``SERVE_PORT``
                        address the ``serve`` subcommand must bind.
"""

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
    output_dir: Path
    serve_host: str
    serve_port: int

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            vendor_base_url=os.environ.get(
                "VENDOR_BASE_URL", "http://localhost:8000"
            ).rstrip("/"),
            client_id=os.environ.get("TF_CLIENT_ID", ""),
            client_secret=os.environ.get("TF_CLIENT_SECRET", ""),
            webhook_secret=os.environ.get("TF_WEBHOOK_SECRET", ""),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "./output")),
            serve_host=os.environ.get("SERVE_HOST", "0.0.0.0"),
            serve_port=int(os.environ.get("SERVE_PORT", "4000")),
        )

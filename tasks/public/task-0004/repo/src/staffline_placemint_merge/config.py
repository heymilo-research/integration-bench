"""Environment configuration. COMPLETE."""

from __future__ import annotations

import dataclasses
import os
from pathlib import Path


@dataclasses.dataclass
class Config:
    staffline_base_url: str
    sl_app_token: str
    sl_hmac_secret: str
    placemint_base_url: str
    pm_client_id: str
    pm_client_secret: str
    output_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            staffline_base_url=os.environ.get("STAFFLINE_BASE_URL", "http://localhost:8000"),
            sl_app_token=os.environ.get("SL_APP_TOKEN", "sl-test-app-token"),
            sl_hmac_secret=os.environ.get("SL_HMAC_SECRET", "sl-test-hmac-secret"),
            placemint_base_url=os.environ.get("PLACEMINT_BASE_URL", "http://localhost:8001"),
            pm_client_id=os.environ.get("PM_CLIENT_ID", "pm-test-client-id"),
            pm_client_secret=os.environ.get("PM_CLIENT_SECRET", "pm-test-client-secret"),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "./output")),
        )

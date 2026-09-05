"""Environment -> Config."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    recruitos_base_url: str
    placemint_base_url: str
    ro_client_id: str
    ro_client_secret: str
    pm_client_id: str
    pm_client_secret: str
    crosswalk_file: Path
    output_dir: Path
    state_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            recruitos_base_url=os.environ.get("VENDOR_BASE_URL", "http://recruitos:8000"),
            placemint_base_url=os.environ.get("PLACEMINT_BASE_URL", "http://placemint:8000"),
            ro_client_id=os.environ.get("RO_CLIENT_ID", ""),
            ro_client_secret=os.environ.get("RO_CLIENT_SECRET", ""),
            pm_client_id=os.environ.get("PM_CLIENT_ID", ""),
            pm_client_secret=os.environ.get("PM_CLIENT_SECRET", ""),
            crosswalk_file=Path(
                os.environ.get("CROSSWALK_FILE", "/app/input/placement_links.csv")
            ),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "./output")),
            state_dir=Path(os.environ.get("STATE_DIR", "./state")),
        )

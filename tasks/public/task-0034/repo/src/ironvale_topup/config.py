"""Environment-driven configuration.

- ``VENDOR_BASE_URL``  base URL of the CrewCall sandbox.
- ``CC_API_KEY``       the static key sent on every data-plane request.
- ``INPUT_FILE``       the agency's weekly placement file.
- ``OUTPUT_DIR``       where the run's artifacts land.
- ``PAGE_LIMIT``       page size the roster sweep asks for.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_INPUT = Path(__file__).resolve().parents[2] / "input" / "placements.csv"
DEFAULT_PAGE_LIMIT = 50


@dataclass(frozen=True)
class Config:
    vendor_base_url: str
    api_key: str
    input_file: Path
    output_dir: Path
    page_limit: int

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            vendor_base_url=os.environ.get(
                "VENDOR_BASE_URL", "http://localhost:8000"
            ).rstrip("/"),
            api_key=os.environ.get("CC_API_KEY", ""),
            input_file=Path(os.environ.get("INPUT_FILE") or DEFAULT_INPUT),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "./output")),
            page_limit=int(os.environ.get("PAGE_LIMIT", str(DEFAULT_PAGE_LIMIT))),
        )

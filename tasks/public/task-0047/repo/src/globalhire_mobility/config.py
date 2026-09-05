from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Config:
    base_url: str
    api_key: str
    input_file: Path
    output_dir: Path

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            base_url=os.environ.get(
                "VENDOR_BASE_URL", "http://localhost:8000"
            ).rstrip("/"),
            api_key=os.environ.get("GH_API_KEY", ""),
            input_file=Path(
                os.environ.get("INPUT_FILE", "input/mobility_actions.csv")
            ),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "output")),
        )

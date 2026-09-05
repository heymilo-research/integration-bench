"""Canonical store (provided). See ``PROBLEM.md``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sorted(rows, key=lambda r: r["source_id"])
    with path.open("w", encoding="utf-8") as fh:
        json.dump(ordered, fh, indent=2, sort_keys=False)

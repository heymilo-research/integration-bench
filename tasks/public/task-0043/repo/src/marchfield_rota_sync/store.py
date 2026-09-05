"""The pass-to-pass state.

One JSON document under ``STATE_DIR``. It carries the watermark the next pass
starts from, one entry per pass already run, and the change ledger itself --
the warehouse loader reads the ledger, so nothing may be dropped from it once
written.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

STATE_FILE = "sync_state.json"


def load(state_dir: Path) -> dict[str, Any]:
    path = Path(state_dir) / STATE_FILE
    if not path.is_file():
        return {"watermark": None, "runs": [], "ledger": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return {"watermark": None, "runs": [], "ledger": []}
    payload.setdefault("watermark", None)
    payload.setdefault("runs", [])
    payload.setdefault("ledger", [])
    return payload


def save(state_dir: Path, payload: dict[str, Any]) -> None:
    path = Path(state_dir)
    path.mkdir(parents=True, exist_ok=True)
    (path / STATE_FILE).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

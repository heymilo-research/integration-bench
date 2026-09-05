"""Durable scratch space between cycles.

``STATE_DIR`` is mounted for us and survives from one cycle to the next;
``OUTPUT_DIR`` is rewritten from scratch every cycle and is not scratch space.
This module is deliberately schema-free: it loads and saves one JSON document
and takes no view on what belongs in it.

    store = StateStore(cfg.state_dir)
    doc = store.load()          # {} on the very first cycle
    ...
    store.save(doc)             # atomic replace
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

STORE_FILENAME = "cycle_state.json"


class StateStore:
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.state_dir / STORE_FILENAME

    def load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return {}
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError:
            return {}
        return doc if isinstance(doc, dict) else {}

    def save(self, doc: dict[str, Any]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(self.state_dir), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(doc, fh, sort_keys=True)
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

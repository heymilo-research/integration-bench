"""Idempotent writeback push loop. See ``PROBLEM.md``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from placemint_summit import resilience
from placemint_summit.client import PlacemintClient


def _load_pending_writes(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return list(data.get("writes", []))


def _idempotency_key(item: dict[str, Any]) -> str:
    """Stable idempotency key for a staged write item."""
    return f"summit-{item['client_ref']}"


def push_one(client: PlacemintClient, item: dict[str, Any]) -> dict[str, Any]:
    """Push one staged item. Returns a result dict per ``PROBLEM.md``; does
    not raise on a normal 4xx rejection."""
    raise NotImplementedError


def push_writes(client: PlacemintClient, pending_writes_path: Path) -> list[dict[str, Any]]:
    """Drain the staged batch; return one result dict per item."""
    raise NotImplementedError

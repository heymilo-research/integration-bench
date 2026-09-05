"""Writeback push loop. See ``PROBLEM.md``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from paygrade_sync.client import PaygradeClient
from paygrade_sync.config import Config

# ---------------------------------------------------------------------------
# Plumbing (provided)
# ---------------------------------------------------------------------------


def read_batch(input_file: Path) -> list[dict[str, Any]]:
    data = json.loads(Path(input_file).read_text(encoding="utf-8"))
    return list(data.get("writes", []))


def write_result(output_dir: Path, results: list[dict[str, Any]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {"writes": sorted(results, key=lambda r: r["client_ref"])}
    out = Path(output_dir) / "writeback_result.json"
    with out.open("w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=2, sort_keys=True)


def request_id_for(client_ref: str) -> str:
    """Stable idempotency handle derived from ``client_ref``."""
    return f"wb:{client_ref}"


# ---------------------------------------------------------------------------
# Push loop
# ---------------------------------------------------------------------------


def push_pending(client: PaygradeClient, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply every staged write and return per-item outcomes (see PROBLEM.md)."""
    raise NotImplementedError


def run_writeback(cfg: Config) -> list[dict[str, Any]]:
    client = PaygradeClient(cfg)
    batch = read_batch(cfg.input_file)
    results = push_pending(client, batch)
    write_result(cfg.output_dir, results)
    return results

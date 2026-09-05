"""Writeback push loop — notes only. See ``PROBLEM.md``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from talentloop_summit.client import TalentLoopClient
from talentloop_summit.config import Config


def read_batch(input_file: Path) -> list[dict[str, Any]]:
    """Load the staged pending-writeback batch as a list of items."""
    data = json.loads(Path(input_file).read_text(encoding="utf-8"))
    return list(data.get("events", []))


def write_push_result(output_dir: Path, results: list[dict[str, Any]]) -> None:
    """Write the recorded push outcome to ``output/writeback_result.json``,
    sorted by ``client_ref`` for a stable, comparable snapshot."""
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {"events": sorted(results, key=lambda r: r["client_ref"])}
    out = Path(output_dir) / "writeback_result.json"
    with out.open("w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=2, sort_keys=True)


def push_pending(
    client: TalentLoopClient, batch: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Apply every staged note-write and return per-item outcomes. See docs/writeback.md."""
    raise NotImplementedError


def run_push(cfg: Config) -> list[dict[str, Any]]:
    client = TalentLoopClient(cfg)
    client.authenticate()
    batch = read_batch(cfg.input_file)
    results = push_pending(client, batch)
    write_push_result(cfg.output_dir, results)
    return results

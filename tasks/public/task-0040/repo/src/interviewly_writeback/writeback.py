"""Event-confirmed reschedule writeback. See ``PROBLEM.md``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from interviewly_writeback.client import InterviewlyClient
from interviewly_writeback.config import Config
from interviewly_writeback.store import Store

WRITEBACKS_TABLE = "writebacks"
_SEQ_STATE_KEY = "writebacks.seq"


# ---------------------------------------------------------------------------
# Result persistence
# ---------------------------------------------------------------------------

def read_batch(input_file: Path) -> list[dict[str, Any]]:
    """Load the staged pending-reschedules batch as a list of items."""
    data = json.loads(Path(input_file).read_text(encoding="utf-8"))
    return list(data.get("reschedules", []))


def write_push_result(output_dir: Path, results: list[dict[str, Any]]) -> None:
    """Write the recorded push outcome to ``output/writeback_result.json``,
    sorted by ``client_ref`` for a stable, comparable snapshot."""
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {"reschedules": sorted(results, key=lambda r: r["client_ref"])}
    out = Path(output_dir) / "writeback_result.json"
    with out.open("w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=2, sort_keys=True)


def _next_seq(store: Store) -> str:
    """Monotonic ordinal for writeback ``updated_at`` bookkeeping."""
    n = int(store.get_state(_SEQ_STATE_KEY) or 0) + 1
    store.set_state(_SEQ_STATE_KEY, n)
    return f"{n:08d}"


# ---------------------------------------------------------------------------
# Push loop
# ---------------------------------------------------------------------------

def push_pending(client: InterviewlyClient, store: Store, batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Apply every staged reschedule exactly once and return one outcome record per item.

    See ``docs/writeback.md`` and PROBLEM.md for outcome shapes.
    """
    raise NotImplementedError


def run_push(cfg: Config) -> list[dict[str, Any]]:
    client = InterviewlyClient(cfg)
    client.authenticate()
    store = Store(cfg.output_dir)
    batch = read_batch(cfg.input_file)
    results = push_pending(client, store, batch)
    write_push_result(cfg.output_dir, results)
    return results


# ---------------------------------------------------------------------------
# Confirmation glue — called from webhooks.py
# ---------------------------------------------------------------------------

def confirm_from_event(store: Store, *, interview_id: str, event_id: str) -> bool:
    """Mark any pending writeback for ``interview_id`` as committed.

    Returns True if a writeback was confirmed, False if nothing matched.
    """
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Poll reconciliation backstop
# ---------------------------------------------------------------------------

def reconcile_pending(client: InterviewlyClient, store: Store) -> int:
    """Poll reconciliation backstop for pending writebacks.

    Returns the number reconciled. See ``docs/writeback.md``.
    """
    raise NotImplementedError

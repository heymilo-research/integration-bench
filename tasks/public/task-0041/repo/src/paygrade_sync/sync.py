"""Polling sync. See ``PROBLEM.md``."""

from __future__ import annotations

from typing import Any

from paygrade_sync.client import PaygradeClient
from paygrade_sync.config import Config
from paygrade_sync.store import Store, Table

EMPLOYEE_WATERMARK = "employee_mod_ms"
ASSIGNMENT_WATERMARK = "assignment_mod_ms"
TOMBSTONE_WATERMARK = "tombstone_since_ms"


def apply_record(table: Table, rec: dict[str, Any]) -> int:
    """Upsert one record with conflict resolution. Returns the record's ``mod_ms``."""
    raise NotImplementedError


def poll_entities(client: PaygradeClient, store: Store) -> None:
    """Back-fill or incrementally reconcile employees and assignments."""
    raise NotImplementedError


def sweep_tombstones(client: PaygradeClient, store: Store) -> None:
    """Apply tombstones since the persisted watermark."""
    raise NotImplementedError


def run_sync(cfg: Config) -> None:
    client = PaygradeClient(cfg)
    store = Store(cfg.output_dir)
    poll_entities(client, store)
    sweep_tombstones(client, store)
    store.flush()

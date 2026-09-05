"""Canonical mapping and polling backfill. See ``PROBLEM.md``."""

from __future__ import annotations

from typing import Any

from interviewly_writeback.client import InterviewlyClient
from interviewly_writeback.config import Config
from interviewly_writeback.store import Store

# table name -> nothing else needed; Interviewly's plural IS the table name.
COLLECTIONS = ("interviews", "panelists", "feedback")
WATERMARK_KEY_FMT = "{table}.since"


def canonicalize_record(table: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Map a raw Interviewly record's fields into the canonical `data` dict."""
    return {k: v for k, v in raw.items() if k not in ("id", "source_id", "updated_at", "is_deleted")}


def apply_record(store: Store, rows: dict[str, dict[str, Any]], table: str, raw: dict[str, Any]) -> bool:
    """Upsert one raw record via the shared canonical mapping.

    Returns True if the row was written, False if it was a stale/out-of-order
    no-op (an already-stored ``updated_at`` that is not older than this
    record's).
    """
    source_id = raw.get("source_id") or raw["id"]
    updated_at = str(raw["updated_at"])
    existing = rows.get(source_id)
    if existing is not None and updated_at < str(existing.get("updated_at", "")):
        return False
    data = canonicalize_record(table, raw)
    Store.upsert(rows, source_id, data, updated_at=updated_at, is_deleted=bool(raw.get("is_deleted", False)))
    return True


def sync(config: Config) -> None:
    store = Store(config.output_dir)
    client = InterviewlyClient(config)
    client.authenticate()

    for table in COLLECTIONS:
        _sync_collection(store, client, table)


def _sync_collection(store: Store, client: InterviewlyClient, table: str) -> None:
    watermark_key = WATERMARK_KEY_FMT.format(table=table)
    modified_since = store.get_state(watermark_key)

    rows = store.load(table)
    max_updated_at = modified_since

    for raw in client.iter_collection(table, modified_since=modified_since):
        apply_record(store, rows, table, raw)
        updated_at = str(raw["updated_at"])
        if max_updated_at is None or updated_at > max_updated_at:
            max_updated_at = updated_at

    store.write(table, rows)
    if max_updated_at is not None:
        store.set_state(watermark_key, max_updated_at)

"""Polling backfill and shared apply-one-event path (provided). See ``PROBLEM.md``."""

from __future__ import annotations

from typing import Any

from interviewly_relay.client import InterviewlyClient
from interviewly_relay.config import Config
from interviewly_relay.store import Store

COLLECTIONS = ("interviews", "panelists", "feedback")
WATERMARK_KEY_FMT = "{table}.since"


def canonicalize_record(table: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Map a raw Interviewly record's fields into the canonical `data` dict."""
    return {k: v for k, v in raw.items() if k not in ("id", "source_id", "updated_at", "is_deleted")}


def apply_polled_record(store: Store, rows: dict[str, dict[str, Any]], table: str, raw: dict[str, Any]) -> bool:
    """Upsert one record from polling. Returns True if written, False if skipped."""
    source_id = raw.get("source_id") or raw["id"]
    updated_at = str(raw["updated_at"])
    existing = rows.get(source_id)
    if existing is not None and updated_at < str(existing.get("updated_at", "")):
        return False
    data = canonicalize_record(table, raw)
    Store.upsert(rows, source_id, data, updated_at=updated_at, is_deleted=bool(raw.get("is_deleted", False)))
    return True


def apply_event(
    store: Store,
    client: InterviewlyClient,
    table: str,
    entity_id: str,
    *,
    event_id: str,
    occurred_at: str | None,
) -> bool:
    """Fetch and apply one webhook event. Returns False if the record does not exist."""
    raw = client.get_one(table, entity_id)
    if raw is None:
        return False
    rows = store.load(table)
    data = canonicalize_record(table, raw)
    Store.upsert(
        rows,
        entity_id,
        data,
        updated_at=occurred_at or str(raw.get("updated_at", "")),
        is_deleted=bool(raw.get("is_deleted", False)),
    )
    store.write(table, rows)
    store.append_journal_entry(entity_id, event_id, occurred_at)
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
        apply_polled_record(store, rows, table, raw)
        updated_at = str(raw["updated_at"])
        if max_updated_at is None or updated_at > max_updated_at:
            max_updated_at = updated_at

    store.write(table, rows)
    if max_updated_at is not None:
        store.set_state(watermark_key, max_updated_at)

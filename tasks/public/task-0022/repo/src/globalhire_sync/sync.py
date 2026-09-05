"""Polling sync: backfill and incremental reconciliation (provided). See ``PROBLEM.md``."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import sqlite3

from globalhire_sync import state, store
from globalhire_sync.client import GlobalHireClient
from globalhire_sync.config import Config

WATERMARK_KEY = "candidate_watermark"


def iso_offset_to_utc_s(value: str) -> int:
    """Parse a wire ISO-8601 timestamp to UTC epoch seconds."""
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.astimezone(timezone.utc).timestamp())


def utc_s_to_iso(utc_s: int) -> str:
    """Encode UTC epoch seconds as an ISO-8601 instant for list filters."""
    return datetime.fromtimestamp(utc_s, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_from_candidate(rec: dict[str, Any]) -> dict[str, Any]:
    """Map a GlobalHire candidate record to a canonical row."""
    source_id = rec["id"]
    updated_at = iso_offset_to_utc_s(rec["modified_at"])
    is_deleted = bool(rec.get("is_deleted", False))
    return {
        "source_id": source_id,
        "data": dict(rec),
        "updated_at": updated_at,
        "is_deleted": is_deleted,
    }


def apply_candidate(conn: sqlite3.Connection, rec: dict[str, Any]) -> int:
    """Apply one candidate with modified_at conflict resolution.

    Returns the record's UTC ``updated_at`` for watermark advancement.
    """
    row = canonical_from_candidate(rec)
    existing = store.get_row(conn, row["source_id"])
    if existing is not None and row["updated_at"] < int(existing["updated_at"]):
        return row["updated_at"]
    if row["is_deleted"]:
        store.tombstone(
            conn,
            source_id=row["source_id"],
            data=row["data"],
            updated_at=row["updated_at"],
        )
    else:
        store.upsert(
            conn,
            source_id=row["source_id"],
            data=row["data"],
            updated_at=row["updated_at"],
            is_deleted=False,
        )
    return row["updated_at"]


def run_sync(cfg: Config, conn: sqlite3.Connection) -> None:
    client = GlobalHireClient(cfg)

    wm_raw = state.get_state(conn, WATERMARK_KEY)
    modified_since: str | None = None
    max_seen = int(wm_raw) if wm_raw is not None else 0
    if wm_raw is not None:
        modified_since = utc_s_to_iso(int(wm_raw))

    for rec in client.iter_candidates(modified_since=modified_since):
        applied_utc_s = apply_candidate(conn, rec)
        max_seen = max(max_seen, applied_utc_s)

    if max_seen > 0:
        state.set_state(conn, WATERMARK_KEY, str(max_seen))

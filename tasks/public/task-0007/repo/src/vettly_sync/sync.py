"""Crawl Vettly's three collections into the canonical store. See ``PROBLEM.md``."""

from __future__ import annotations

from typing import Any

import sqlite3

from vettly_sync import state, store
from vettly_sync.auth import VettlyAuth
from vettly_sync.client import CursorExpiredError, VettlyClient
from vettly_sync.config import Config

COLLECTIONS = ("subjects", "checks", "reports")


def run_sync(cfg: Config, conn: sqlite3.Connection) -> None:
    auth = VettlyAuth(cfg.vendor_base_url, cfg.client_id, cfg.client_secret)
    client = VettlyClient(cfg.vendor_base_url, auth)
    for entity in COLLECTIONS:
        _sync_entity(conn, client, entity)


def _canonicalize(raw: dict[str, Any], entity: str) -> dict[str, Any]:
    data = {
        k: v for k, v in raw.items()
        if k not in ("id", "source_id", "updated_at", "is_deleted")
    }
    if entity == "reports":
        data["completed_at"] = data.pop("finished_at", None)
    return {
        "source_id": raw.get("source_id") or raw["id"],
        "data": data,
        "updated_at": int(raw["updated_at"]),
        "is_deleted": bool(raw.get("is_deleted", False)),
    }


def _apply_page(conn: sqlite3.Connection, entity: str, page: dict[str, Any], max_seen: int) -> int:
    for raw in page.get("data", []):
        row = _canonicalize(raw, entity)
        store.upsert(
            conn,
            entity,
            source_id=row["source_id"],
            data=row["data"],
            updated_at=row["updated_at"],
            is_deleted=row["is_deleted"],
        )
        if row["updated_at"] > max_seen:
            max_seen = row["updated_at"]
    return max_seen


def _sync_entity(conn: sqlite3.Connection, client: VettlyClient, entity: str) -> None:
    cursor_key = f"{entity}.cursor"
    pass_since_key = f"{entity}.pass_since"
    watermark_key = f"{entity}.watermark"

    cursor = state.get_state(conn, cursor_key)
    watermark = state.get_state(conn, watermark_key)

    if cursor is not None:
        modified_since: str | None = state.get_state(conn, pass_since_key)
    else:
        modified_since = watermark if watermark is not None else "0"
        state.set_state(conn, pass_since_key, modified_since)

    max_seen = int(watermark) if watermark is not None else 0

    while True:
        try:
            page = client.list_page(entity, cursor=cursor, modified_since=modified_since)
        except CursorExpiredError:
            # recover and continue the crawl.
            state.clear_state(conn, cursor_key)
            cursor = None
            modified_since = str(max_seen)
            state.set_state(conn, pass_since_key, modified_since)
            conn.commit()
            page = client.list_page(entity, cursor=None, modified_since=modified_since)

        max_seen = _apply_page(conn, entity, page, max_seen)

        cursor = page.get("cursor")
        if cursor is not None:
            state.set_state(conn, cursor_key, cursor)
            state.set_state(conn, watermark_key, str(max_seen))
            conn.commit()
            continue

        state.clear_state(conn, cursor_key)
        state.clear_state(conn, pass_since_key)
        state.set_state(conn, watermark_key, str(max_seen))
        conn.commit()
        break

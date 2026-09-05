"""Canonical store (provided). See ``PROBLEM.md``."""

from __future__ import annotations

import json
from typing import Any

def _load_data(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes)):
        import json
        return json.loads(value)
    return {}


import sqlite3
from pathlib import Path


def connect(database_url: str) -> sqlite3.Connection:
    """Open a sqlite connection (callers commit). File path from DATABASE_URL."""
    path = database_url
    if path.startswith("sqlite:////"):
        path = "/" + path[len("sqlite:////"):]
    elif path.startswith("sqlite:///"):
        path = path[len("sqlite:///"):]
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(path)


def ensure_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS canonical_candidates (
            source_id   text PRIMARY KEY,
            data        TEXT NOT NULL,
            updated_at  bigint NOT NULL,
            is_deleted  INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS canonical_sync_state (
            key   text PRIMARY KEY,
            value text NOT NULL
        )
        """
    )
    conn.commit()


def upsert(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    data: dict[str, Any],
    updated_at: int,
    is_deleted: bool = False,
) -> None:
    """Insert or update a candidate row keyed by ``source_id``."""
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO canonical_candidates (source_id, data, updated_at, is_deleted)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (source_id) DO UPDATE
          SET data = EXCLUDED.data,
              updated_at = EXCLUDED.updated_at,
              is_deleted = EXCLUDED.is_deleted
        """,
        (source_id, json.dumps(data), int(updated_at), bool(is_deleted)),
    )
    conn.commit()


def tombstone(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    data: dict[str, Any],
    updated_at: int,
) -> None:
    """Mark a candidate deleted, retaining the row as a tombstone."""
    upsert(
        conn,
        source_id=source_id,
        data=data,
        updated_at=updated_at,
        is_deleted=True,
    )


def get_row(conn: sqlite3.Connection, source_id: str) -> dict[str, Any] | None:
    """Return the stored row for ``source_id`` (or None)."""
    cur = conn.cursor()
    cur.execute(
        "SELECT source_id, data, updated_at, is_deleted "
        "FROM canonical_candidates WHERE source_id = ?",
        (source_id,),
    )
    r = cur.fetchone()
    if r is None:
        return None
    return {"source_id": r[0], "data": _load_data(r[1]), "updated_at": r[2], "is_deleted": bool(r[3])}


def all_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Return every stored row, sorted by ``source_id`` (for snapshots/tests)."""
    cur = conn.cursor()
    cur.execute(
        "SELECT source_id, data, updated_at, is_deleted "
        "FROM canonical_candidates ORDER BY source_id"
    )
    rows = cur.fetchall()
    return [
        {"source_id": r[0], "data": _load_data(r[1]), "updated_at": r[2], "is_deleted": bool(r[3])}
        for r in rows
    ]

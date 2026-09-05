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

_TABLES = {"candidate": "candidates", "application": "applications"}


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
    for table in _TABLES.values():
        cur.execute(
            f"""
            CREATE TABLE IF NOT EXISTS canonical_{table} (
                source_id   text PRIMARY KEY,
                data        TEXT NOT NULL,
                updated_at  text NOT NULL,
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


def _table(kind: str) -> str:
    try:
        return _TABLES[kind]
    except KeyError:
        raise ValueError(f"unknown entity kind {kind!r}") from None


def upsert(
    conn: sqlite3.Connection,
    kind: str,
    *,
    source_id: str,
    data: dict[str, Any],
    updated_at: Any,
    is_deleted: bool = False,
) -> None:
    """Insert or update a row keyed by ``source_id`` in the ``kind`` table."""
    table = _table(kind)
    cur = conn.cursor()
    cur.execute(
        f"""
        INSERT INTO canonical_{table} (source_id, data, updated_at, is_deleted)
        VALUES (?, ?, ?, ?)
        ON CONFLICT (source_id) DO UPDATE
          SET data = EXCLUDED.data,
              updated_at = EXCLUDED.updated_at,
              is_deleted = EXCLUDED.is_deleted
        """,
        (source_id, json.dumps(data), str(updated_at), bool(is_deleted)),
    )
    conn.commit()


def tombstone(
    conn: sqlite3.Connection,
    kind: str,
    *,
    source_id: str,
    data: dict[str, Any],
    updated_at: Any,
) -> None:
    """Mark a row deleted, retaining it as a tombstone."""
    upsert(
        conn, kind,
        source_id=source_id,
        data=data,
        updated_at=updated_at,
        is_deleted=True,
    )


def get_row(conn: sqlite3.Connection, kind: str, source_id: str) -> dict[str, Any] | None:
    table = _table(kind)
    cur = conn.cursor()
    cur.execute(
        f"SELECT source_id, data, updated_at, is_deleted "
        f"FROM canonical_{table} WHERE source_id = ?",
        (source_id,),
    )
    r = cur.fetchone()
    if r is None:
        return None
    return {"source_id": r[0], "data": _load_data(r[1]), "updated_at": r[2], "is_deleted": bool(r[3])}


def all_rows(conn: sqlite3.Connection, kind: str) -> list[dict[str, Any]]:
    """Return every stored row for ``kind``, sorted by ``source_id``."""
    table = _table(kind)
    cur = conn.cursor()
    cur.execute(
        f"SELECT source_id, data, updated_at, is_deleted "
        f"FROM canonical_{table} ORDER BY source_id"
    )
    rows = cur.fetchall()
    return [
        {"source_id": r[0], "data": _load_data(r[1]), "updated_at": r[2], "is_deleted": bool(r[3])}
        for r in rows
    ]


# ---------------------------------------------------------------------------
# sync_state (watermarks, dedup bookkeeping)
# ---------------------------------------------------------------------------

def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    cur = conn.cursor()
    cur.execute("SELECT value FROM canonical_sync_state WHERE key = ?", (key,))
    r = cur.fetchone()
    return r[0] if r is not None else None


def set_state(conn: sqlite3.Connection, key: str, value: Any) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO canonical_sync_state (key, value)
        VALUES (?, ?)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        (key, str(value)),
    )
    conn.commit()

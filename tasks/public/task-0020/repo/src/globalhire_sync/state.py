"""Key/value sync state helpers (watermarks, cursors). See ``PROBLEM.md``."""

from __future__ import annotations

import sqlite3


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    cur = conn.cursor()
    cur.execute("SELECT value FROM canonical_sync_state WHERE key = ?", (key,))
    r = cur.fetchone()
    return r[0] if r is not None else None


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
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

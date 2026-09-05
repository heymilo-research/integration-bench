"""Canonical store (provided). See ``PROBLEM.md``."""

from __future__ import annotations

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
        CREATE TABLE IF NOT EXISTS canonical_bulk_items (
            client_ref    text PRIMARY KEY,
            created       INTEGER NOT NULL,
            candidate_id  text
        )
        """
    )
    conn.commit()


def upsert_result(
    conn: sqlite3.Connection,
    *,
    client_ref: str,
    created: bool,
    candidate_id: str | None = None,
) -> None:
    """Insert or overwrite the durable outcome for one client_ref."""
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO canonical_bulk_items (client_ref, created, candidate_id)
        VALUES (?, ?, ?)
        ON CONFLICT (client_ref) DO UPDATE
          SET created = EXCLUDED.created,
              candidate_id = EXCLUDED.candidate_id
        """,
        (client_ref, bool(created), candidate_id),
    )
    conn.commit()


def get_result(conn: sqlite3.Connection, client_ref: str) -> dict[str, Any] | None:
    """Return the durable row for ``client_ref`` (or None if never resolved)."""
    cur = conn.cursor()
    cur.execute(
        "SELECT client_ref, created, candidate_id FROM canonical_bulk_items WHERE client_ref = ?",
        (client_ref,),
    )
    r = cur.fetchone()
    if r is None:
        return None
    return {"client_ref": r[0], "created": bool(r[1]), "candidate_id": r[2]}


def known_refs(conn: sqlite3.Connection) -> set[str]:
    """Every client_ref with a durable resolved outcome."""
    cur = conn.cursor()
    cur.execute("SELECT client_ref FROM canonical_bulk_items")
    return {r[0] for r in cur.fetchall()}


def all_results(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every resolved row, sorted by client_ref (for snapshots/tests)."""
    cur = conn.cursor()
    cur.execute(
        "SELECT client_ref, created, candidate_id FROM canonical_bulk_items ORDER BY client_ref"
    )
    rows = cur.fetchall()
    return [{"client_ref": r[0], "created": bool(r[1]), "candidate_id": r[2]} for r in rows]

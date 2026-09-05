"""Canonical store (provided). See ``PROBLEM.md``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import sqlite3

ENTITY_KINDS = ("candidate", "job", "application", "note")


def to_canonical(row: dict[str, Any], id_field: str = "id") -> dict[str, Any]:
    """Map one StaffLine API row to the canonical shape."""
    return {
        "source_id": row[id_field],
        "data": {k: v for k, v in row.items() if k != id_field},
        "updated_at": int(row.get("mod_ts", 0)),
        "is_deleted": False,
    }


def sorted_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(rows, key=lambda r: r["source_id"])


def write_json(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted_rows(rows), indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# SQLite-backed persistence
# ---------------------------------------------------------------------------


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
        CREATE TABLE IF NOT EXISTS canonical_records (
            entity      text NOT NULL,
            source_id   text NOT NULL,
            data        TEXT NOT NULL,
            updated_at  bigint NOT NULL,
            is_deleted  INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (entity, source_id)
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS canonical_watermarks (
            key   text PRIMARY KEY,
            value bigint NOT NULL
        )
        """
    )
    conn.commit()


def upsert_record(conn: sqlite3.Connection, entity: str, canonical_row: dict[str, Any]) -> None:
    """Insert or overwrite one canonical row (by entity + source_id)."""
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO canonical_records (entity, source_id, data, updated_at, is_deleted)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT (entity, source_id) DO UPDATE
          SET data = EXCLUDED.data,
              updated_at = EXCLUDED.updated_at,
              is_deleted = EXCLUDED.is_deleted
        """,
        (
            entity,
            canonical_row["source_id"],
            json.dumps(canonical_row["data"]),
            canonical_row["updated_at"],
            canonical_row["is_deleted"],
        ),
    )
    conn.commit()


def tombstone_record(conn: sqlite3.Connection, entity: str, source_id: str, deleted_at: int) -> None:
    """Mark a canonical row deleted (row retained)."""
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE canonical_records
           SET is_deleted = true, updated_at = ?
         WHERE entity = ? AND source_id = ?
        """,
        (deleted_at, entity, source_id),
    )
    if cur.rowcount == 0:
        cur.execute(
            """
            INSERT INTO canonical_records (entity, source_id, data, updated_at, is_deleted)
            VALUES (?, ?, '{}', ?, true)
            ON CONFLICT (entity, source_id) DO NOTHING
            """,
            (entity, source_id, deleted_at),
        )
    conn.commit()


def _load_data(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes)):
        return json.loads(value)
    return {}


def all_records(conn: sqlite3.Connection, entity: str) -> list[dict[str, Any]]:
    cur = conn.cursor()
    cur.execute(
        "SELECT source_id, data, updated_at, is_deleted FROM canonical_records "
        "WHERE entity = ? ORDER BY source_id",
        (entity,),
    )
    rows = cur.fetchall()
    return [
        {
            "source_id": r[0],
            "data": _load_data(r[1]),
            "updated_at": r[2],
            "is_deleted": bool(r[3]),
        }
        for r in rows
    ]


def get_watermark(conn: sqlite3.Connection, key: str) -> int:
    cur = conn.cursor()
    cur.execute("SELECT value FROM canonical_watermarks WHERE key = ?", (key,))
    row = cur.fetchone()
    return int(row[0]) if row else 0


def set_watermark(conn: sqlite3.Connection, key: str, value: int) -> None:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO canonical_watermarks (key, value) VALUES (?, ?)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
        """,
        (key, int(value)),
    )
    conn.commit()


def dump_all(conn: sqlite3.Connection, output_dir: Path, filenames: dict[str, str]) -> None:
    """Snapshot the current sqlite state to one JSON file per entity kind."""
    for entity, filename in filenames.items():
        write_json(output_dir / filename, all_records(conn, entity))

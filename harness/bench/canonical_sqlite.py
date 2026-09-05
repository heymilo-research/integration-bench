"""SQLite helpers for Integration-Bench canonical stores (lean path).

File-backed only — path must outlive ``docker compose run`` containers via a
shared ``/data`` volume or host bind-mount.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

# In-container path; compose binds this to a durable volume or directory.
CANONICAL_DB_PATH = "/data/canonical.db"
CANONICAL_DATABASE_URL = f"sqlite:///{CANONICAL_DB_PATH}"


def sqlite_path_from_url(database_url: str) -> str:
    """Parse ``sqlite:///…`` / ``sqlite:////abs`` / bare path → filesystem path."""
    url = (database_url or "").strip()
    if url.startswith("sqlite:////"):
        return "/" + url[len("sqlite:////") :]
    if url.startswith("sqlite:///"):
        rest = url[len("sqlite:///") :]
        return rest if rest.startswith("/") else rest
    if url.startswith("sqlite://"):
        # sqlite://localhost/path uncommon; treat as path after host
        return url.split("sqlite://", 1)[-1].lstrip("/")
    return url


def connect(database_url: str) -> sqlite3.Connection:
    path = sqlite_path_from_url(database_url)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(path)


def reset_db_file(path: str | Path = CANONICAL_DB_PATH) -> None:
    p = Path(path)
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    for suffix in ("-wal", "-shm"):
        try:
            Path(str(p) + suffix).unlink()
        except FileNotFoundError:
            pass


def reset_canonical_on_stack(stack) -> None:
    """Wipe the canonical sqlite file through the canonical stack contract."""
    stack.reset_canonical_db()

"""Webhook listener. See ``PROBLEM.md``."""

from __future__ import annotations

import sqlite3

from connector.config import Config


def serve(
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    max_events: int | None = None,
    idle_timeout: float | None = None,
    max_runtime: float | None = None,
) -> None:
    raise NotImplementedError

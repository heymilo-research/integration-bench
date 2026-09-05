"""Polling sync. See ``PROBLEM.md``."""

from __future__ import annotations

import sqlite3

from globalhire_sync.config import Config


def run_sync(cfg: Config, conn: sqlite3.Connection) -> None:
    raise NotImplementedError

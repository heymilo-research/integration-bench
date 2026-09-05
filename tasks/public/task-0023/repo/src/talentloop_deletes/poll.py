"""Polling sync with delete reconciliation. See ``PROBLEM.md``."""

from __future__ import annotations

import sqlite3

from talentloop_deletes import store, sync
from talentloop_deletes.client import TalentLoopClient
from talentloop_deletes.config import Config


def _reconcile_one(conn: sqlite3.Connection, client: TalentLoopClient, kind: str, entity_id: str) -> None:
    raise NotImplementedError


def run_poll(cfg: Config, conn: sqlite3.Connection, client: TalentLoopClient | None = None) -> None:
    """One full polling pass over candidates and applications."""
    raise NotImplementedError

"""Polling sync for all four entities, with delete reconciliation. See ``PROBLEM.md``."""

from __future__ import annotations

import sqlite3

from talentloop_summit import store, sync
from talentloop_summit.client import TalentLoopClient
from talentloop_summit.config import Config

_KINDS = ("candidate", "job", "application", "note")


def _iter_for_kind(client: TalentLoopClient, kind: str):
    raise NotImplementedError


def _get_for_kind(client: TalentLoopClient, kind: str):
    raise NotImplementedError


def _reconcile_one(conn: sqlite3.Connection, client: TalentLoopClient, kind: str, entity_id: str) -> None:
    raise NotImplementedError


def _poll_one_kind(cfg: Config, conn: sqlite3.Connection, client: TalentLoopClient, kind: str) -> None:
    raise NotImplementedError


def run_poll(cfg: Config, conn: sqlite3.Connection, client: TalentLoopClient | None = None) -> None:
    """Full list pass over all four entities, then reconcile vanished ids. Safe to call repeatedly."""
    raise NotImplementedError

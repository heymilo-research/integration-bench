"""One-time backfill (all 4 entities) + recurring poll (job/note only) (provided). See ``PROBLEM.md``."""

from __future__ import annotations

from typing import Any

import sqlite3

from talentloop_reliable import store, sync
from talentloop_reliable.client import TalentLoopClient
from talentloop_reliable.config import Config

_BACKFILL_KINDS = ("candidate", "job", "application", "note")
_RECURRING_KINDS = ("job", "note")

_ITER = {
    "candidate": lambda client: client.iter_candidates(),
    "job": lambda client: client.iter_jobs(),
    "application": lambda client: client.iter_applications(),
    "note": lambda client: client.iter_notes(),
}

_GET = {
    "candidate": lambda client: client.get_candidate,
    "job": lambda client: client.get_job,
    "application": lambda client: client.get_application,
    "note": lambda client: client.get_note,
}


def run_backfill(cfg: Config, conn: sqlite3.Connection, client: TalentLoopClient | None = None) -> None:
    """One-time full backfill of all four entities into the canonical store."""
    if client is None:
        client = TalentLoopClient(cfg)
        client.authenticate()
    for kind in _BACKFILL_KINDS:
        for rec in _ITER[kind](client):
            sync.APPLY[kind](conn, rec)


def _reconcile_one(conn: sqlite3.Connection, client: TalentLoopClient, kind: str, entity_id: str) -> None:
    getter = _GET[kind](client)
    result = getter(entity_id)
    if result.gone:
        sync.mark_deleted(conn, kind, entity_id)
    elif result.ok and result.record is not None:
        # Stale race: the id reappeared between the list pass and this
        # confirm call (e.g. a page boundary shift). Apply it, don't tombstone.
        sync.APPLY[kind](conn, result.record)
    # result.not_found: should not normally happen for a previously-known id.
    # Leave the row untouched rather than guessing.


def _poll_one_kind(conn: sqlite3.Connection, client: TalentLoopClient, kind: str) -> None:
    seen_ids: set[str] = set()
    for rec in _ITER[kind](client):
        source_id = rec.get("source_id") or rec["id"]
        seen_ids.add(source_id)
        sync.APPLY[kind](conn, rec)

    previously_live = store.live_ids(conn, kind)
    vanished = previously_live - seen_ids
    for entity_id in sorted(vanished):
        _reconcile_one(conn, client, kind, entity_id)


def run_poll(cfg: Config, conn: sqlite3.Connection, client: TalentLoopClient | None = None) -> None:
    """One recurring poll cycle. Safe to call repeatedly."""
    if client is None:
        client = TalentLoopClient(cfg)
        client.authenticate()
    for kind in _RECURRING_KINDS:
        _poll_one_kind(conn, client, kind)

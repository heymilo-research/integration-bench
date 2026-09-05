"""Canonical mapping + one-time backfill (provided). See ``PROBLEM.md``."""

from __future__ import annotations

from typing import Any

import sqlite3

from talentloop_deletes import store
from talentloop_deletes.client import TalentLoopClient
from talentloop_deletes.config import Config


def canonical_from_candidate(rec: dict[str, Any]) -> dict[str, Any]:
    source_id = rec.get("source_id") or rec["id"]
    updated_at = str(rec["modified_at"])
    data = {k: v for k, v in rec.items() if k != "source_id"}
    return {"source_id": source_id, "data": data, "updated_at": updated_at, "is_deleted": False}


def canonical_from_application(rec: dict[str, Any]) -> dict[str, Any]:
    source_id = rec.get("source_id") or rec["id"]
    updated_at = str(rec["modified_at"])
    data = {k: v for k, v in rec.items() if k != "source_id"}
    return {"source_id": source_id, "data": data, "updated_at": updated_at, "is_deleted": False}


def apply_candidate(conn: sqlite3.Connection, rec: dict[str, Any]) -> bool:
    """Upsert one LIVE candidate record. Returns True if written, False if a
    stale/out-of-order no-op (an existing row with a strictly newer
    ``updated_at`` is never regressed)."""
    row = canonical_from_candidate(rec)
    existing = store.get_row(conn, "candidate", row["source_id"])
    if existing is not None and not existing["is_deleted"] and str(row["updated_at"]) < str(existing["updated_at"]):
        return False
    store.upsert(conn, "candidate", source_id=row["source_id"], data=row["data"],
                 updated_at=row["updated_at"], is_deleted=False)
    return True


def apply_application(conn: sqlite3.Connection, rec: dict[str, Any]) -> bool:
    """Upsert one LIVE application record (same rule as :func:`apply_candidate`)."""
    row = canonical_from_application(rec)
    existing = store.get_row(conn, "application", row["source_id"])
    if existing is not None and not existing["is_deleted"] and str(row["updated_at"]) < str(existing["updated_at"]):
        return False
    store.upsert(conn, "application", source_id=row["source_id"], data=row["data"],
                 updated_at=row["updated_at"], is_deleted=False)
    return True


_APPLY = {"candidate": apply_candidate, "application": apply_application}


def mark_deleted(conn: sqlite3.Connection, kind: str, source_id: str) -> None:
    """Tombstone ``source_id`` in the canonical store."""
    existing = store.get_row(conn, kind, source_id)
    if existing is not None:
        store.tombstone(conn, kind, source_id=source_id, data=existing["data"],
                        updated_at=existing["updated_at"])
    else:
        store.tombstone(conn, kind, source_id=source_id,
                        data={"id": source_id, "source_id": source_id}, updated_at="")


def run_backfill(cfg: Config, conn: sqlite3.Connection) -> None:
    """One-time full backfill of candidates + applications into an (initially
    empty) canonical store. No reconcile sweep is needed here -- there is
    nothing "previously known" yet to diff against."""
    client = TalentLoopClient(cfg)
    client.authenticate()

    for rec in client.iter_candidates():
        apply_candidate(conn, rec)
    for rec in client.iter_applications():
        apply_application(conn, rec)

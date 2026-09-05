"""Canonical mapping + one-time backfill (provided). See ``PROBLEM.md``."""

from __future__ import annotations

from typing import Any

import sqlite3

from talentforge_hooks import store
from talentforge_hooks.client import TalentForgeClient
from talentforge_hooks.config import Config


def canonical_from_candidate(rec: dict[str, Any]) -> dict[str, Any]:
    """Map a TalentForge candidate record to a canonical row."""
    source_id = rec.get("source_id") or rec["id"]
    updated_at = int(rec["modified_at"])
    is_deleted = bool(rec.get("is_deleted", False))
    data = {k: v for k, v in rec.items() if k != "source_id"}
    return {
        "source_id": source_id,
        "data": data,
        "updated_at": updated_at,
        "is_deleted": is_deleted,
    }


def canonical_from_application(rec: dict[str, Any]) -> dict[str, Any]:
    """Map a TalentForge application record to a canonical row."""
    source_id = rec.get("source_id") or rec["id"]
    updated_at = str(rec["modified_at"])
    is_deleted = bool(rec.get("is_deleted", False))
    data = {k: v for k, v in rec.items() if k != "source_id"}
    return {
        "source_id": source_id,
        "data": data,
        "updated_at": updated_at,
        "is_deleted": is_deleted,
    }


def apply_candidate(conn: sqlite3.Connection, rec: dict[str, Any]) -> bool:
    """Apply one candidate record with conflict resolution.

    Returns True if written, False if stale/out-of-order no-op.
    """
    row = canonical_from_candidate(rec)
    existing = store.get_row(conn, "candidate", row["source_id"])
    if existing is not None and int(row["updated_at"]) < int(existing["updated_at"]):
        return False
    if row["is_deleted"]:
        store.tombstone(conn, "candidate", source_id=row["source_id"],
                        data=row["data"], updated_at=row["updated_at"])
    else:
        store.upsert(conn, "candidate", source_id=row["source_id"],
                     data=row["data"], updated_at=row["updated_at"], is_deleted=False)
    return True


def apply_application(conn: sqlite3.Connection, rec: dict[str, Any]) -> bool:
    """Apply one application record with conflict resolution."""
    row = canonical_from_application(rec)
    existing = store.get_row(conn, "application", row["source_id"])
    if existing is not None and str(row["updated_at"]) < str(existing["updated_at"]):
        return False
    if row["is_deleted"]:
        store.tombstone(conn, "application", source_id=row["source_id"],
                        data=row["data"], updated_at=row["updated_at"])
    else:
        store.upsert(conn, "application", source_id=row["source_id"],
                     data=row["data"], updated_at=row["updated_at"], is_deleted=False)
    return True


def run_backfill(cfg: Config, conn: sqlite3.Connection) -> None:
    """One-time full backfill of candidates and applications."""
    client = TalentForgeClient(cfg)
    client.authenticate()

    for rec in client.iter_candidates():
        apply_candidate(conn, rec)
    for rec in client.iter_applications():
        apply_application(conn, rec)

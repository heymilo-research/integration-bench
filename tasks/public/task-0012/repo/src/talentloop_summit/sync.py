"""Canonical mapping + one-time backfill (provided) for all 4 entities. See ``PROBLEM.md``."""

from __future__ import annotations

from typing import Any

import sqlite3

from talentloop_summit import store
from talentloop_summit.client import TalentLoopClient
from talentloop_summit.config import Config

_KINDS = ("candidate", "job", "application", "note")


def _canonical(rec: dict[str, Any]) -> dict[str, Any]:
    source_id = rec.get("source_id") or rec["id"]
    updated_at = str(rec["modified_at"])
    data = {k: v for k, v in rec.items() if k != "source_id"}
    return {"source_id": source_id, "data": data, "updated_at": updated_at, "is_deleted": False}


def canonical_from_candidate(rec: dict[str, Any]) -> dict[str, Any]:
    return _canonical(rec)


def canonical_from_job(rec: dict[str, Any]) -> dict[str, Any]:
    return _canonical(rec)


def canonical_from_application(rec: dict[str, Any]) -> dict[str, Any]:
    return _canonical(rec)


def canonical_from_note(rec: dict[str, Any]) -> dict[str, Any]:
    return _canonical(rec)


_CANONICAL = {
    "candidate": canonical_from_candidate,
    "job": canonical_from_job,
    "application": canonical_from_application,
    "note": canonical_from_note,
}


def _apply(conn: sqlite3.Connection, kind: str, rec: dict[str, Any]) -> bool:
    """Upsert one live record. Returns True if written, False if stale no-op."""
    row = _CANONICAL[kind](rec)
    existing = store.get_row(conn, kind, row["source_id"])
    if existing is not None and not existing["is_deleted"] and str(row["updated_at"]) < str(existing["updated_at"]):
        return False
    store.upsert(conn, kind, source_id=row["source_id"], data=row["data"],
                 updated_at=row["updated_at"], is_deleted=False)
    return True


def apply_candidate(conn: sqlite3.Connection, rec: dict[str, Any]) -> bool:
    return _apply(conn, "candidate", rec)


def apply_job(conn: sqlite3.Connection, rec: dict[str, Any]) -> bool:
    return _apply(conn, "job", rec)


def apply_application(conn: sqlite3.Connection, rec: dict[str, Any]) -> bool:
    return _apply(conn, "application", rec)


def apply_note(conn: sqlite3.Connection, rec: dict[str, Any]) -> bool:
    return _apply(conn, "note", rec)


APPLY = {
    "candidate": apply_candidate,
    "job": apply_job,
    "application": apply_application,
    "note": apply_note,
}


def mark_deleted(conn: sqlite3.Connection, kind: str, source_id: str) -> None:
    """Tombstone ``source_id`` in the canonical store."""
    existing = store.get_row(conn, kind, source_id)
    if existing is not None:
        store.tombstone(conn, kind, source_id=source_id, data=existing["data"],
                        updated_at=existing["updated_at"])
    else:
        store.tombstone(conn, kind, source_id=source_id,
                        data={"id": source_id, "source_id": source_id}, updated_at="")


def _iter_for_kind(client: TalentLoopClient, kind: str):
    return {
        "candidate": client.iter_candidates,
        "job": client.iter_jobs,
        "application": client.iter_applications,
        "note": client.iter_notes,
    }[kind]()


def run_backfill(cfg: Config, conn: sqlite3.Connection) -> None:
    """One-time full backfill of all four entities into an (initially empty) store."""
    client = TalentLoopClient(cfg)
    client.authenticate()

    for kind in _KINDS:
        for rec in _iter_for_kind(client, kind):
            APPLY[kind](conn, rec)

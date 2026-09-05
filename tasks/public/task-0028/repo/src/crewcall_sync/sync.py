"""Drift-aware crawl-to-convergence and canonical mapping. See ``PROBLEM.md``."""

from __future__ import annotations

from typing import Any

from crewcall_sync.client import CrewCallClient
from crewcall_sync.config import Config
from crewcall_sync.store import Store

_PAGE_LIMIT = 10  # the vendor's documented default page size.


def crawl_to_convergence(
    client: CrewCallClient, collection: str, limit: int = _PAGE_LIMIT
) -> dict[str, dict[str, Any]]:
    """Dedupe-by-id, re-crawl-until-a-clean-pass snapshot of ``collection``."""
    known: dict[str, dict[str, Any]] = {}
    while True:
        changed_this_pass = 0
        for rec in client.crawl_one_pass(collection, limit):
            rid = rec["id"]
            prev = known.get(rid)
            if prev is None or prev != rec:
                changed_this_pass += 1
            known[rid] = rec
        if changed_this_pass == 0:
            break
    return known


def canonical_from_record(rec: dict[str, Any]) -> dict[str, Any]:
    """Map a raw CrewCall record to a canonical row
    ``{source_id, data, updated_at, is_deleted}``."""
    return {
        "source_id": rec["id"],
        "data": dict(rec),
        "updated_at": rec.get("updated_at"),
        "is_deleted": bool(rec.get("is_deleted", False)),
    }


def apply_collection(store: Store, entity: str, known: dict[str, dict[str, Any]]) -> None:
    for rec in known.values():
        row = canonical_from_record(rec)
        if row["is_deleted"]:
            store.tombstone(
                entity,
                source_id=row["source_id"],
                data=row["data"],
                updated_at=row["updated_at"],
            )
        else:
            store.upsert(
                entity,
                source_id=row["source_id"],
                data=row["data"],
                updated_at=row["updated_at"],
                is_deleted=False,
            )


_COLLECTIONS = {
    "worker": "workers",
    "gig": "gigs",
    "assignment": "assignments",
}


def run_sync(cfg: Config, store: Store) -> None:
    client = CrewCallClient(cfg)
    for entity, collection in _COLLECTIONS.items():
        known = crawl_to_convergence(client, collection)
        apply_collection(store, entity, known)

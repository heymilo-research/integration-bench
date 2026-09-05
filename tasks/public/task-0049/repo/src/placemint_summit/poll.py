"""Paginated polling + reconciliation. See ``PROBLEM.md``."""

from __future__ import annotations

from typing import Any

from placemint_summit import resilience, store as store_mod
from placemint_summit.client import PlacemintClient
from placemint_summit.store import Store

PAGE_LIMIT = 100

_COLLECTIONS: dict[str, str] = {
    "placements": "/api/placements",
    "clients": "/api/clients",
    "notes": "/api/notes",
}


def poll_collection(
    client: PlacemintClient, kind: str, *, modified_since: str | None = None
) -> list[dict[str, Any]]:
    """Paginate the collection endpoint to exhaustion."""
    raise NotImplementedError


def apply_page(store: Store, kind: str, records: list[dict[str, Any]]) -> None:
    """Apply a page of records and advance the watermark."""
    raise NotImplementedError


def run_full_backfill(client: PlacemintClient, store: Store) -> None:
    """Backfill all three collections from scratch."""
    raise NotImplementedError


def run_reconcile(client: PlacemintClient, store: Store) -> None:
    """Incremental reconciliation pass from current watermarks."""
    raise NotImplementedError

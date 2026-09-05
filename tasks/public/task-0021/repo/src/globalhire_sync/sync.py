"""Per-collection fetch logic: candidates, placements, agencies. See ``PROBLEM.md``."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from globalhire_sync.client import GlobalHireClient

DEFAULT_LIMIT = 100

_WIRE_ISO = "%Y-%m-%dT%H:%M:%S"


def parse_wire_timestamp(value: str) -> int:
    """Parse a GlobalHire wire timestamp to UTC epoch seconds.

    See docs/entities.md for the stated format.
    """
    dt = datetime.strptime(value.strip()[:19], _WIRE_ISO).replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def canonical_record(rec: dict[str, Any]) -> dict[str, Any]:
    """Map one raw GlobalHire record to the canonical row shape. ``data`` is
    the raw wire record, unmodified (so the real field names are preserved
    verbatim)."""
    return {
        "source_id": rec["id"],
        "data": dict(rec),
        "updated_at": parse_wire_timestamp(rec["modified_at"]),
        "is_deleted": bool(rec.get("is_deleted", False)),
    }


def _fetch_v1_collection(client: GlobalHireClient, collection: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for resp in client.iter_offset_pages(f"/v1/{collection}", limit=DEFAULT_LIMIT):
        for rec in resp.body.get("data", []):
            rows.append(canonical_record(rec))
    return rows


def fetch_candidates(client: GlobalHireClient) -> list[dict[str, Any]]:
    """Fetch every candidate record for this tenant."""
    return _fetch_v1_collection(client, "candidates")


def fetch_placements(client: GlobalHireClient) -> list[dict[str, Any]]:
    """Fetch every placement record for this tenant."""
    return _fetch_v1_collection(client, "placements")


def fetch_agencies(client: GlobalHireClient) -> list[dict[str, Any]]:
    """Fetch every agency record for this tenant."""
    return _fetch_v1_collection(client, "agencies")

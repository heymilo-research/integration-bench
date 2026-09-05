"""Bullpen v2 client: cursor-paginated reads and REST writeback."""

from __future__ import annotations

from urllib.parse import urlencode

from staffline_to_bullpen_migrate import bullpen_mapping as mapping
from staffline_to_bullpen_migrate.bullpen_transport import BullpenTransport


class BullpenClient:
    def __init__(self, base_url: str, client_id: str, client_secret: str) -> None:
        self._transport = BullpenTransport(base_url, client_id, client_secret)

    def fetch_all(self, kind: str) -> list[dict]:
        """Full cursor-paginated backfill of one /v2/* entity collection,
        normalized to canonical field names/timestamps."""
        normalize = mapping.NORMALIZERS[kind]
        rows: list[dict] = []
        cursor: str | None = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            qs = f"?{urlencode(params)}" if params else ""
            page = self._transport.get(f"/v2/{kind}{qs}")
            rows.extend(normalize(r) for r in page["data"])
            cursor = page.get("cursor")
            if cursor is None:
                return rows

    def update_candidate(self, candidate_id: str, fields: dict, idempotency_key: str) -> dict:
        return self._transport.patch(f"/v2/candidates/{candidate_id}", fields, idempotency_key=idempotency_key)

    def create_note(self, candidate_id: str, body: str, author: str, idempotency_key: str) -> dict:
        return self._transport.post(
            f"/v2/candidates/{candidate_id}/notes",
            {"body": body, "author": author},
            idempotency_key=idempotency_key,
        )

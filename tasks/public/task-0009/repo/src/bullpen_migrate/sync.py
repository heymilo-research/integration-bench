"""Paginated collection fetch for /v2/<entity> (provided). See ``PROBLEM.md``."""

from __future__ import annotations

from urllib.parse import urlencode

from bullpen_migrate import mapping
from bullpen_migrate.client import BullpenClient


def fetch_collection(client: BullpenClient, kind: str, modified_since_wire: str | None = None) -> list[dict]:
    rows: list[dict] = []
    cursor: str | None = None
    first = True
    normalize = mapping.NORMALIZERS[kind]
    while True:
        params: dict[str, str] = {}
        if cursor:
            params["cursor"] = cursor
        if first and modified_since_wire is not None:
            params["modified_since"] = modified_since_wire
        first = False
        qs = f"?{urlencode(params)}" if params else ""
        page = client.get(f"/v2/{kind}{qs}")
        rows.extend(normalize(r) for r in page["data"])
        cursor = page.get("cursor")
        if cursor is None:
            return rows

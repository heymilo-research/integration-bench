"""Reading the roster.

One walk from `offset=0` to the short page that ends it. Per the Workforce
Analytics note (`docs/fenmarsh-census-runbook.md`) that walk is what the census
counts.
"""

from __future__ import annotations

from typing import Any

from fenmarsh_census.client import CrewCallClient


def sweep_roster(client: CrewCallClient) -> list[dict[str, Any]]:
    """Every worker record one forward walk of the roster is served.

    The page size the envelope reports back is the one the vendor actually
    applied, so the offset steps by that rather than by whatever was asked for.
    """
    records: list[dict[str, Any]] = []
    offset = 0
    while True:
        envelope = client.worker_page(offset=offset)
        rows = envelope.get("data") or []
        records.extend(rows)
        served = int(envelope.get("limit") or 0) or client.cfg.page_limit
        if len(rows) < served:
            return records
        offset += served

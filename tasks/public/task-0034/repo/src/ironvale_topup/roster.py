"""Reading the CrewCall roster, and looking a crew member up in it.

The roster re-sorts while it is paged (``docs/pagination.md``), so the sweep
dedupes by ``id`` and re-crawls from the start until a whole pass turns up no
new id. The churn is finite, so that pass arrives.
"""

from __future__ import annotations

from typing import Any

from ironvale_topup.client import CrewCallClient


def sweep_roster(client: CrewCallClient) -> list[dict[str, Any]]:
    """Every worker record the sweep managed to see, deduped by id."""
    known: dict[str, dict[str, Any]] = {}
    while True:
        discovered = 0
        offset = 0
        while True:
            envelope = client.worker_page(offset=offset)
            rows = envelope.get("data") or []
            for record in rows:
                if str(record["id"]) not in known:
                    known[str(record["id"])] = record
                    discovered += 1
            if len(rows) < client.cfg.page_limit:
                break
            offset += client.cfg.page_limit
        if discovered == 0:
            return [known[k] for k in sorted(known)]


def build_index(roster: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The roster keyed by address, which is how the staffing desk identifies
    a crew member on CrewCall."""
    index: dict[str, dict[str, Any]] = {}
    for worker in roster:
        index[str(worker.get("email") or "").strip().lower()] = worker
    return index


def resolve(crew_email: str, index: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """The CrewCall worker this crew member is, or None if CrewCall has none."""
    return index.get(str(crew_email or "").strip().lower())

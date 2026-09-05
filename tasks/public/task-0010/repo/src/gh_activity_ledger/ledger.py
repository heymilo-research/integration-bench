"""Place every GlobalHire record in the audit period its last activity falls in.

``client.py`` is the transport layer, ``windows.py`` reads Compliance's audit
calendar and ``store.py`` owns both output files. Nothing here talks HTTP or
formats anything: it turns the tenant into ledger rows.

WHAT ``build_ledger`` RETURNS

``LedgerResult.rows`` — one entry per record placed in a period::

    {"window_id": "W0", "entity": "candidates",
     "record_id": "cand_00000", "outcome": "created"}

``entity`` is the collection's plural name (``candidates``, ``placements``,
``agencies``). ``outcome`` is one of:

    ``deleted``   the record is flagged deleted
    ``created``   its created and modified stamps are the same instant
    ``updated``   anything else

``LedgerResult.scanned`` — how many records the run read off GlobalHire, per
collection, whether or not they ended up in a period.

``store.py`` derives the per-period counts, the live/deleted split and the
outside-every-period totals from those two fields, so they only have to be
right once.

HOW A PERIOD IS BOUNDED

Straight off the runbook (``docs/ledger-window-runbook.md``, "Chunking the
extract"): one request per period per collection, bounded by the incremental
pair — ``modified_since`` for the inclusive start, ``modified_until`` for the
exclusive end. The response is that period, so every record it carries is a
record of that period; nothing here has to re-check a stamp it already asked
the server to filter on.

Distinct ids across the whole extract are counted for the tenant totals the
audit pack compares against GlobalHire's UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from gh_activity_ledger.client import GlobalHireClient
from gh_activity_ledger.windows import Window

COLLECTIONS = ("candidates", "placements", "agencies")

OUTCOME_CREATED = "created"
OUTCOME_UPDATED = "updated"
OUTCOME_DELETED = "deleted"


@dataclass
class LedgerResult:
    """What one ledger run produced.

    ``rows``     one entry per record placed in a period (see the module docstring).
    ``scanned``  records read off GlobalHire, per collection.
    """

    rows: list[dict[str, Any]] = field(default_factory=list)
    scanned: dict[str, int] = field(default_factory=dict)


def outcome_for(record: dict[str, Any]) -> str:
    """The ledger outcome for one record."""
    if record.get("is_deleted"):
        return OUTCOME_DELETED
    if record.get("created_at") == record.get("modified_at"):
        return OUTCOME_CREATED
    return OUTCOME_UPDATED


def build_ledger(client: GlobalHireClient, windows: list[Window]) -> LedgerResult:
    """Build the audit-period activity ledger for the tenant."""
    rows: list[dict[str, Any]] = []
    scanned: dict[str, int] = {}

    for collection in COLLECTIONS:
        seen: set[str] = set()
        for window in windows:
            period = client.crawl(
                collection,
                modified_since=window.starts_at,
                modified_until=window.ends_at,
            )
            for record in period:
                record_id = str(record.get("id"))
                seen.add(record_id)
                rows.append({
                    "window_id": window.window_id,
                    "entity": collection,
                    "record_id": record_id,
                    "outcome": outcome_for(record),
                })
        scanned[collection] = len(seen)

    return LedgerResult(rows=rows, scanned=scanned)

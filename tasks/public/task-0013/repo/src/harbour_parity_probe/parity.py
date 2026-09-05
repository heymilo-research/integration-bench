"""The nightly pass.

Reads the warehouse snapshot the previous run left behind, brings it back in
line with RecruitOS, and reports every divergence it repaired.

Everything around this module works: transport, auth, paging and retries are in
``client.py``, the snapshot reader is in ``snapshot.py``, the webhook listener
is in ``listener.py``, and the three artifacts are serialised by ``report.py``.

The desk note in ``docs/harbour-parity-desk-note.md`` is where this pass's
current design came from.
"""

from __future__ import annotations

from harbour_parity_probe.client import RecruitOSClient
from harbour_parity_probe.report import Divergence, ParityResult
from harbour_parity_probe.snapshot import CANONICAL_FIELD, COLLECTIONS, ENTITY_NAME, Snapshot


def run_parity(*, client: RecruitOSClient, snapshot: Snapshot) -> ParityResult:
    """Catch the warehouse up with RecruitOS.

    Asks RecruitOS for everything it has touched since the position the last run
    recorded, folds it into the warehouse's copy of each collection, and reports
    what moved.
    """
    result = ParityResult(synced_through=snapshot.synced_through)

    for collection in COLLECTIONS:
        field = CANONICAL_FIELD[collection]
        entity = ENTITY_NAME[collection]
        held = snapshot.held(collection)

        changed, _total = client.crawl(
            collection, modified_since=snapshot.synced_through or None
        )
        for record in changed:
            record_id = str(record.get("id") or "")
            if not record_id:
                continue
            value = str(record.get(field) or "")
            if record.get("is_deleted"):
                if record_id in held:
                    result.rows.append(
                        Divergence(entity, record_id, "remove",
                                   str(held[record_id].get(field) or ""), "")
                    )
                    del held[record_id]
                continue
            if record_id not in held:
                result.rows.append(Divergence(entity, record_id, "add", "", value))
                held[record_id] = {
                    "id": record_id,
                    field: value,
                    "updated_at": str(record.get("updated_at") or ""),
                }
                continue
            if str(held[record_id].get(field) or "") != value:
                result.rows.append(
                    Divergence(entity, record_id, "update",
                               str(held[record_id].get(field) or ""), value)
                )
                held[record_id][field] = value
                held[record_id]["updated_at"] = str(record.get("updated_at") or "")

        result.census[collection] = len(held)

    return result

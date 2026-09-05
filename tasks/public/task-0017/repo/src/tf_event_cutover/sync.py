"""The nightly candidate pass.

This is the file the cut-over lands in. What it does today is what
`brightmoor-sync` has done since the beginning: every pass lists the whole
`candidates` collection and rebuilds the mirror out of the result. That is the
pass being retired -- it is the reason the tenant's `GET /rest/*` budget is
permanently at its ceiling, and it is why Brightmoor bought the event
subscription.

The pass writes `mirror.json`, `change_ledger.json` and `state.json` through
`store.flush()` every time, so a run that completes always leaves a
loader-readable set of artifacts behind.
"""

from __future__ import annotations

from typing import Any

from tf_event_cutover.client import TalentForgeClient
from tf_event_cutover.config import Config
from tf_event_cutover.store import Store

COLLECTION = "candidates"


def _high_watermark(store: Store) -> str | None:
    stamps = [r.get("updated_at") for r in store.rows() if r.get("updated_at")]
    return max(stamps) if stamps else None


def run_sync(cfg: Config) -> dict[str, Any]:
    store = Store(cfg.output_dir)
    run_number = store.note_run()
    client = TalentForgeClient(cfg)

    records = client.crawl(COLLECTION)
    for record in records:
        store.upsert_from_vendor(record)

    store.set_watermark(_high_watermark(store))

    summary: dict[str, Any] = {
        "run": run_number,
        "fetched": len(records),
        "held": store.count(),
        "ledger_rows": len(store.ledger()),
        "gets": client.gets,
    }
    store.flush()
    return summary

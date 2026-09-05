"""Lightweight sanity tests for the HireWire connector. See ``PROBLEM.md``."""

from __future__ import annotations

from pathlib import Path

from hirewire_connector import sync
from hirewire_connector.store import Store

REPO_ROOT = Path(__file__).resolve().parents[1]
BATCH = REPO_ROOT / "input" / "pending_events.json"


def test_read_batch_shape() -> None:
    events = sync.read_batch(BATCH)
    assert len(events) == 3
    for item in events:
        assert item["client_ref"], "every staged event carries a stable client_ref"
        assert item["candidate_id"], "every staged event targets a candidate"


def test_client_refs_unique() -> None:
    events = sync.read_batch(BATCH)
    refs = [e["client_ref"] for e in events]
    assert len(refs) == len(set(refs)), "client_refs must be unique"


def test_store_roundtrips_sorted(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.upsert(source_id="cand_0002", data={"id": "cand_0002"}, updated_at=200)
    store.upsert(source_id="cand_0001", data={"id": "cand_0001"}, updated_at=100)
    store.flush()

    reloaded = Store(tmp_path)
    rows = reloaded.rows()
    assert [r["source_id"] for r in rows] == ["cand_0001", "cand_0002"]
    assert all("updated_at" in r and "is_deleted" in r for r in rows)


def test_store_watermark_persists(tmp_path: Path) -> None:
    store = Store(tmp_path)
    assert store.get_state("candidate_watermark") is None
    store.set_state("candidate_watermark", 1773482460)
    store.flush()
    assert Store(tmp_path).get_state("candidate_watermark") == 1773482460

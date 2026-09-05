import json
from pathlib import Path

from crewcall_sync.store import Store


def test_upsert_and_flush(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.upsert(
        "worker",
        source_id="wkr_0001",
        data={"id": "wkr_0001", "first_name": "Ada"},
        updated_at="2026-05-04T08:00:00Z",
    )
    store.flush()
    out = json.loads((tmp_path / "workers.json").read_text(encoding="utf-8"))
    assert out == [
        {
            "source_id": "wkr_0001",
            "data": {"id": "wkr_0001", "first_name": "Ada"},
            "updated_at": "2026-05-04T08:00:00Z",
            "is_deleted": False,
        }
    ]


def test_tombstone_retains_row(tmp_path: Path) -> None:
    store = Store(tmp_path)
    store.tombstone(
        "worker",
        source_id="wkr_0002",
        data={"id": "wkr_0002"},
        updated_at="2026-05-04T09:00:00Z",
    )
    rows = store.rows("worker")
    assert len(rows) == 1
    assert rows[0]["is_deleted"] is True


def test_rows_sorted_by_source_id(tmp_path: Path) -> None:
    store = Store(tmp_path)
    for sid in ("wkr_0003", "wkr_0001", "wkr_0002"):
        store.upsert("worker", source_id=sid, data={"id": sid}, updated_at="x")
    ids = [r["source_id"] for r in store.rows("worker")]
    assert ids == ["wkr_0001", "wkr_0002", "wkr_0003"]

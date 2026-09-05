"""Store-helper tests. Run: pytest"""

import json

from staffline_sync.store import sorted_rows, to_canonical, write_json


def test_to_canonical_shapes_row_and_strips_id():
    row = {
        "id": "cand_0001",
        "fname": "Ada",
        "lname": "Lovelace",
        "mod_ts": 1546819260000,
    }
    out = to_canonical(row)
    assert out["source_id"] == "cand_0001"
    assert out["is_deleted"] is False
    assert out["updated_at"] == 1546819260000
    assert "id" not in out["data"]
    assert out["data"]["fname"] == "Ada"


def test_sorted_rows_orders_by_source_id():
    rows = [{"source_id": "cand_0002"}, {"source_id": "cand_0001"}]
    assert [r["source_id"] for r in sorted_rows(rows)] == ["cand_0001", "cand_0002"]


def test_write_json_sorts_and_round_trips(tmp_path):
    rows = [
        {"source_id": "cand_0002", "data": {}, "updated_at": 2, "is_deleted": False},
        {"source_id": "cand_0001", "data": {}, "updated_at": 1, "is_deleted": False},
    ]
    path = tmp_path / "out" / "candidates.json"
    write_json(path, rows)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert [r["source_id"] for r in loaded] == ["cand_0001", "cand_0002"]

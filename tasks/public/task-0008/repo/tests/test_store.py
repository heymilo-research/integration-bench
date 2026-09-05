"""A couple of sanity checks for the canonical store plumbing (already provided). See ``PROBLEM.md``."""

from __future__ import annotations

from pathlib import Path

from staffline_fullsync.store import Store


def test_upsert_then_tombstone_retains_row(tmp_path: Path) -> None:
    store = Store(tmp_path)
    rows: dict[str, dict] = {}
    store.upsert(rows, "cand_0001", {"fname": "Ada"}, 100)
    assert rows["cand_0001"]["is_deleted"] is False

    store.tombstone(rows, "cand_0001", 200)
    assert rows["cand_0001"]["is_deleted"] is True
    # tombstone retains the last-known data and advances updated_at.
    assert rows["cand_0001"]["data"] == {"fname": "Ada"}
    assert rows["cand_0001"]["updated_at"] == 200


def test_write_is_sorted_by_source_id(tmp_path: Path) -> None:
    store = Store(tmp_path)
    rows: dict[str, dict] = {}
    store.upsert(rows, "cand_0002", {"fname": "B"}, 2)
    store.upsert(rows, "cand_0001", {"fname": "A"}, 1)
    store.write("candidates", rows)

    import json

    written = json.loads((tmp_path / "candidates.json").read_text())
    assert [r["source_id"] for r in written] == ["cand_0001", "cand_0002"]


def test_state_roundtrip(tmp_path: Path) -> None:
    store = Store(tmp_path)
    assert store.get_state("tombstones.since") is None
    store.set_state("tombstones.since", 12345)
    assert store.get_state("tombstones.since") == 12345

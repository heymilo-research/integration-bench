"""Tests for the canonical store plumbing (store.py). See ``PROBLEM.md``."""

from __future__ import annotations

from vettly_sync.store import Store


def test_upsert_then_load_roundtrip(tmp_path):
    store = Store(tmp_path)
    rows = store.load("subjects")
    assert rows == {}

    store.upsert(
        rows,
        "sub_0001",
        {"full_name": "Ada Lovelace", "email": "ada.lovelace@subjects.test"},
        updated_at=1773480000,
        is_deleted=False,
    )
    store.write("subjects", rows)

    reloaded = store.load("subjects")
    assert set(reloaded) == {"sub_0001"}
    row = reloaded["sub_0001"]
    assert row["source_id"] == "sub_0001"
    assert row["data"]["full_name"] == "Ada Lovelace"
    assert row["is_deleted"] is False
    assert row["updated_at"] == 1773480000


def test_upsert_soft_delete_retains_row(tmp_path):
    store = Store(tmp_path)
    rows = store.load("subjects")
    store.upsert(rows, "sub_0007", {"full_name": "Bob"}, updated_at=1773480000, is_deleted=False)
    store.upsert(rows, "sub_0007", {"full_name": "Bob"}, updated_at=1773480075, is_deleted=True)

    assert "sub_0007" in rows  # row retained, not removed
    assert rows["sub_0007"]["is_deleted"] is True
    assert rows["sub_0007"]["updated_at"] == 1773480075


def test_state_get_set_roundtrip(tmp_path):
    store = Store(tmp_path)
    assert store.get_state("subjects.since") is None
    store.set_state("subjects.since", 1773480000)
    assert store.get_state("subjects.since") == 1773480000

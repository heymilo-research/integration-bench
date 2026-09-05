"""Unit tests for store plumbing and canonicalization. See ``PROBLEM.md``."""

from __future__ import annotations

import pytest

from vettly_sync.store import _check_table
from vettly_sync.sync import _canonicalize


def test_check_table_accepts_known_tables():
    for table in ("subjects", "checks", "reports"):
        _check_table(table)  # must not raise


def test_check_table_rejects_unknown_table():
    with pytest.raises(ValueError):
        _check_table("widgets")


def test_canonicalize_subject_passthrough():
    raw = {
        "id": "sub_0001",
        "source_id": "sub_0001",
        "full_name": "Ada Lovelace",
        "email": "ada.lovelace@subjects.test",
        "created_at": 1773480000,
        "updated_at": 1773480000,
        "is_deleted": False,
    }
    row = _canonicalize(raw, "subjects")
    assert row["source_id"] == "sub_0001"
    assert row["updated_at"] == 1773480000
    assert row["is_deleted"] is False
    assert row["data"]["full_name"] == "Ada Lovelace"
    assert "id" not in row["data"]
    assert "source_id" not in row["data"]
    assert "updated_at" not in row["data"]


def test_canonicalize_report_maps_finished_at_to_completed_at():
    raw = {
        "id": "rpt_0001",
        "source_id": "rpt_0001",
        "check_id": "chk_0001",
        "result": "clear",
        "created_at": 1773480000,
        "updated_at": 1773480100,
        "finished_at": 1773480100,
        "is_deleted": False,
    }
    row = _canonicalize(raw, "reports")
    assert row["data"]["completed_at"] == 1773480100
    assert "finished_at" not in row["data"]


def test_canonicalize_updated_at_is_int_not_string():
    raw = {
        "id": "chk_0001",
        "source_id": "chk_0001",
        "subject_id": "sub_0001",
        "check_type": "criminal",
        "status": "pending",
        "created_at": 1773480000,
        "updated_at": 1773480000,
        "is_deleted": False,
    }
    row = _canonicalize(raw, "checks")
    assert isinstance(row["updated_at"], int)

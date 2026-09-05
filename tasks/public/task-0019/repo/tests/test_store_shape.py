"""Shape tests for the artifact writer (the part that is already finished)."""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vaultd_cutover.store import FeedStore  # noqa: E402


def _entry(record_id: str, kind: str, op: str) -> dict:
    return {
        "record_id": record_id,
        "kind": kind,
        "op": op,
        "subject_id": "<id>",
        "subject_email": "someone@example.invalid",
        "updated_at": 0,
        "detail": "<value>",
    }


def test_write_derives_tallies_and_retirement_list(tmp_path: Path) -> None:
    store = FeedStore(tmp_path)
    result = store.write(
        [
            _entry("b", "check", "retire"),
            _entry("a", "subject", "upsert"),
            _entry("c", "report", "upsert"),
        ],
        cursor_used=0,
        next_cursor=0,
    )

    assert result["record_count"] == 3
    assert result["counts"] == {
        "subject": 1, "check": 1, "report": 1, "upsert": 2, "retire": 1,
    }
    assert result["retired_ids"] == ["b"]
    assert [row["record_id"] for row in result["changes"]] == ["a", "b", "c"]

    on_disk = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert on_disk == result


def test_loader_file_carries_one_row_per_entry(tmp_path: Path) -> None:
    store = FeedStore(tmp_path)
    store.write(
        [_entry("a", "subject", "upsert"), _entry("b", "check", "retire")],
        cursor_used=0,
        next_cursor=0,
    )
    with (tmp_path / "import_report.csv").open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert [row["record_id"] for row in rows] == ["a", "b"]
    assert [row["op"] for row in rows] == ["upsert", "retire"]
    assert set(rows[0]) == {
        "record_id", "kind", "op", "subject_id", "subject_email",
    }

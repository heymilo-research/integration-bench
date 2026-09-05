"""Unit tests for the parts of the sweep that do not need a live tenant."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from talentforge_legal_hold.roster import RosterRow  # noqa: E402
from talentforge_legal_hold.sweep import (  # noqa: E402
    crawl,
    custodian_row,
    index_by_email,
    notes_for,
)


class FakeClient:
    """Serves cursor pages the way docs/pagination.md describes them: every
    page carries a `cursor`, and the last page's is `null`. List records are
    whole candidate records, per the handover note."""

    def __init__(self, pages: dict[str, list[list[dict]]]) -> None:
        self.pages = pages
        self.calls: list[tuple[str, str | None]] = []

    def get(self, path, params=None):
        cursor = (params or {}).get("cursor")
        self.calls.append((path, cursor))
        index = 0 if cursor is None else int(cursor)
        batch = self.pages[path][index]
        last = index + 1 >= len(self.pages[path])
        return {"data": batch, "cursor": None if last else str(index + 1)}


def test_crawl_follows_cursors_until_the_null_cursor():
    client = FakeClient({"/c": [[{"id": 1}, {"id": 2}], [{"id": 3}]]})
    assert [r["id"] for r in crawl(client, "/c")] == [1, 2, 3]
    assert client.calls == [("/c", None), ("/c", "1")]


def test_crawl_of_an_empty_collection_still_makes_exactly_one_request():
    client = FakeClient({"/c": [[]]})
    assert crawl(client, "/c") == []
    assert client.calls == [("/c", None)]


def test_notes_for_walks_the_subcollection_past_the_first_page():
    client = FakeClient({"/candidates/cand_1/notes": [[{"id": "n1"}], [{"id": "n2"}]]})
    assert [n["id"] for n in notes_for(client, "cand_1")] == ["n1", "n2"]


def test_index_by_email_keeps_every_holder_of_a_shared_address():
    index = index_by_email([
        {"id": "cand_1", "email": "A@X.test"},
        {"id": "cand_2", "email": "a@x.test"},
        {"id": "cand_3", "email": "b@x.test"},
    ])
    assert [c["id"] for c in index["a@x.test"]] == ["cand_1", "cand_2"]


def test_custodian_row_maps_the_documented_candidate_and_note_timestamps():
    row = custodian_row(
        RosterRow(matter_ref="LH-1", email="a@x.test"),
        {
            "id": "cand_1",
            "given_name": "Ada",
            "family_name": "Hopper",
            "phone": "+1-555-0001",
            "pipeline_status": "new",
            "is_deleted": False,
            "created_at": 1773482400000,
            "updatedAt": "2026-03-14T10:00:30Z",
        },
        [{"id": "note_1", "author": "r@tf.test", "body": "b",
          "created_at": "2026-03-14T10:00:00Z"}],
    )
    assert row["created_at"] == "2026-03-14T10:00:00Z"
    assert row["updated_at"] == "2026-03-14T10:00:30Z"
    assert row["phone"] == "+1-555-0001"
    assert row["notes"][0]["created_at"] == "2026-03-14T10:00:00Z"


def test_custodian_row_of_a_childless_candidate_carries_an_empty_note_list():
    row = custodian_row(
        RosterRow(matter_ref="LH-2", email="b@x.test"),
        {"id": "cand_2", "given_name": "B", "family_name": "C",
         "phone": "+1-555-0002", "pipeline_status": "new", "is_deleted": False,
         "created_at": 1773482400000, "updatedAt": "2026-03-14T10:00:00Z"},
        [],
    )
    assert row["notes"] == []

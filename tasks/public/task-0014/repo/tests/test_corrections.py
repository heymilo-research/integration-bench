from __future__ import annotations

from hirewire_corrections.corrections import (
    CORRECTION_STAGE_FROM,
    CORRECTION_STAGE_TO,
    discover_backlog,
)


class _FakeClient:
    """Fake ``list_candidates`` for ``discover_backlog`` tests."""

    def __init__(self, pages: list[list[dict]], total: int | None = None) -> None:
        self._pages = pages
        self._total = total if total is not None else sum(len(p) for p in pages)
        self.calls: list[tuple[int, int]] = []

    def list_candidates(self, page: int = 1, per_page: int = 100, modified_since=None):
        self.calls.append((page, per_page))
        idx = page - 1
        data = self._pages[idx] if idx < len(self._pages) else []
        return 200, {"data": data, "page": page, "per_page": per_page, "total": self._total}


def test_discover_backlog_filters_to_screening_only() -> None:
    pages = [
        [
            {"id": "cand_0001", "stage": "screening", "is_deleted": False},
            {"id": "cand_0002", "stage": "hired", "is_deleted": False},
        ]
    ]
    client = _FakeClient(pages)
    backlog = discover_backlog(client)
    assert [r["id"] for r in backlog] == ["cand_0001"]


def test_discover_backlog_excludes_deleted_screening_rows() -> None:
    pages = [
        [
            {"id": "cand_0003", "stage": "screening", "is_deleted": True},
            {"id": "cand_0004", "stage": "screening", "is_deleted": False},
        ]
    ]
    client = _FakeClient(pages)
    backlog = discover_backlog(client)
    assert [r["id"] for r in backlog] == ["cand_0004"]


def test_discover_backlog_sorts_by_id_across_pages() -> None:
    pages = [
        [{"id": "cand_0020", "stage": "screening", "is_deleted": False}],
        [{"id": "cand_0005", "stage": "screening", "is_deleted": False}],
    ]
    client = _FakeClient(pages, total=150)
    backlog = discover_backlog(client)
    assert [r["id"] for r in backlog] == ["cand_0005", "cand_0020"]
    assert client.calls == [(1, 100), (2, 100)]


def test_correction_constants() -> None:
    assert CORRECTION_STAGE_FROM == "screening"
    assert CORRECTION_STAGE_TO == "rejected"

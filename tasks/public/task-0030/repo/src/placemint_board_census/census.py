"""Reconcile the desk board against the Placemint placement book.

Straight off the desk board runbook (`docs/meridian-desk-board-runbook.md`):

- the board's `placemint_ref` is the Placemint id as Placemint issues it, so the
  file loads into a dict keyed on the ref and a plain lookup resolves a row;
- the ref points at the engagement the row is about, so there is nothing to
  check the candidate and client columns against;
- Placemint takes a placement out of the book when the desk closes it out, so a
  ref the book does not answer for has been closed upstream and everything the
  book does return is live.

One pass over the board, one pass over the book, and whatever the board did not
claim is unclaimed.
"""

from __future__ import annotations

from typing import Any

from placemint_board_census.board import read_board
from placemint_board_census.client import PlacemintClient
from placemint_board_census.config import Config
from placemint_board_census.report import ReportWriter

CENSUS_AUTHOR = "boardcensus@meridian.test"
CENSUS_NOTE = "Desk board census: no Meridian board row for this placement (stage: {stage})."


def read_book(client: PlacemintClient) -> list[dict[str, Any]]:
    """Every placement record Placemint currently holds, verbatim."""
    book: list[dict[str, Any]] = []
    offset = 0
    while True:
        envelope = client.placement_page(offset=offset)
        book.extend(envelope.get("data") or [])
        offset += int(envelope.get("limit") or 100)
        if offset >= int(envelope.get("total") or 0):
            return book


def resolve_row(row: dict[str, Any], book: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """One board row's verdict and the placement id it is about (or None)."""
    entry: dict[str, Any] = {
        "board_ref": row.get("board_ref"),
        "verdict": "unmatched",
        "placement_id": None,
        "source_line": row.get("source_line"),
    }
    record = book.get((row.get("placemint_ref") or "").strip())
    if record is not None:
        entry["verdict"] = "matched"
        entry["placement_id"] = str(record["id"])
    return entry


def run_board_census(cfg: Config) -> dict[str, Any]:
    client = PlacemintClient(cfg)
    writer = ReportWriter(cfg.output_dir)

    board = read_board(cfg.board_file)
    book = read_book(client)
    by_id = {str(record["id"]): record for record in book}

    rows = [resolve_row(row, by_id) for row in board]
    claimed = {row["placement_id"] for row in rows if row["verdict"] == "matched"}

    unclaimed: list[dict[str, Any]] = []
    for record in book:
        placement_id = str(record["id"])
        if placement_id in claimed:
            continue
        stage = str(record.get("stage"))
        status, note = client.create_note(
            placement_id, CENSUS_NOTE.format(stage=stage), CENSUS_AUTHOR,
            idempotency_key=f"census:{placement_id}",
        )
        unclaimed.append({
            "placement_id": placement_id,
            "client_id": str(record.get("client_id")),
            "stage": stage,
            "note_id": note.get("id") if status in (200, 201) and isinstance(note, dict) else None,
        })

    report = writer.write(rows, unclaimed)
    return {
        "board_row_count": report["board_row_count"],
        "matched_count": report["matched_count"],
        "retired_count": report["retired_count"],
        "unmatched_count": report["unmatched_count"],
        "unclaimed_count": report["unclaimed_count"],
        "book_size": len(book),
        "pages_fetched": client.pages_fetched,
        "notes_created": client.notes_created,
        "token_mints": client.token_mints,
    }

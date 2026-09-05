"""The nightly case-note mirror.

Rosterly has no case-note collection: a note hangs off the carer it was written
about, so the mirror walks the roster and asks each carer for theirs. That has
run every night since the pilot.

The rules Ellerby work to are in ``docs/ellerby-case-note-mirror-note.md``;
Rosterly's own documentation is in ``docs/``.

``client.py``, ``config.py`` and ``report.py`` carry the plumbing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ellerby_note_mirror.client import RosterlyClient
from ellerby_note_mirror.config import Config
from ellerby_note_mirror.report import ReportWriter


def utc_column(value: str) -> str:
    """A note stamp as a canonical UTC instant."""
    moment = datetime.strptime(str(value).strip(), "%Y-%m-%dT%H:%M:%S").replace(
        tzinfo=timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def note_row(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "note_id": str(record["id"]),
        "worker_id": str(record["worker_id"]),
        "author": str(record.get("author") or ""),
        "body": str(record.get("body") or ""),
        "created_utc": utc_column(record["created_at"]),
        "updated_utc": utc_column(record["updated_at"]),
        "state": "retired" if record.get("is_deleted") else "active",
    }


def roster(client: RosterlyClient) -> list[dict[str, Any]]:
    """Every carer on the tenant's roster, paged out."""
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        outcome = client.roster_page(offset)
        if not outcome.ok:
            return rows
        envelope = outcome.body or {}
        page = envelope.get("data") or []
        rows.extend(page)
        used = int(envelope.get("limit") or 0) or max(len(page), 1)
        offset += used
        if not page or offset >= int(envelope.get("total") or 0):
            return rows


def crew_to_poll(crew: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Which carers this run asks for notes.

    Every carer the roster returned.
    """
    return list(crew)


def notes_for(client: RosterlyClient, worker_id: str) -> list[dict[str, Any]]:
    """One carer's case notes, following the supplied mirror note."""
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        outcome = client.notes_page(worker_id, offset)
        if not outcome.ok:
            return rows
        envelope = outcome.body or {}
        page = envelope.get("data") or []
        rows.extend(page)
        used = int(envelope.get("limit") or 0) or max(len(page), 1)
        offset += used
        if not page or offset >= int(envelope.get("total") or 0):
            return rows


def run_mirror(cfg: Config) -> dict[str, Any]:
    client = RosterlyClient(cfg)
    crew = roster(client)
    if not crew:
        raise RuntimeError("the roster came back empty; nothing to mirror")

    polled = crew_to_poll(crew)
    rows: list[dict[str, Any]] = []
    for carer in polled:
        for record in notes_for(client, str(carer["id"])):
            rows.append(note_row(record))
    rows.sort(key=lambda row: row["note_id"])

    summary = {
        "workers_on_roster": len(crew),
        "workers_polled": len(polled),
        "note_count": len(rows),
        "active_note_count": sum(1 for row in rows if row["state"] == "active"),
        "retired_note_count": sum(1 for row in rows if row["state"] == "retired"),
    }
    ReportWriter(cfg.output_dir).write(rows, summary)
    return dict(summary, requests_made=client.requests_made)

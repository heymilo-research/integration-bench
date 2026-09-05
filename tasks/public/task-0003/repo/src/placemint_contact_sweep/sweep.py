"""Sweep Meridian's open Placemint pipeline for placements nobody has contacted.

Written from `docs/` and Delivery Ops' runbook
(`docs/meridian-contact-sweep-runbook.md`), which is where the two shortcuts
here come from:

- The note feed arrives oldest first, so the last row a placement appears on is
  its current note: page the feed, let each row overwrite the previous one in a
  dict keyed on `placement_id`, and the dict holds every placement's latest
  note when the crawl ends. No comparator needed.

- There is no reason to drag the whole note history over the wire. The feed
  takes `modified_since` (`docs/pagination.md`), so it is narrowed to the review
  horizon the sweep is measuring against: anything older than the horizon is
  older than the horizon, and cannot change the answer.

Scope, the chase-up wording and the idempotency key are PROBLEM.md's rules.
"""

from __future__ import annotations

from typing import Any

from placemint_contact_sweep.client import PlacemintClient
from placemint_contact_sweep.config import Config
from placemint_contact_sweep.report import ReportWriter

CHASE_AUTHOR = "sweep@meridian.test"
CHASE_WITH_DATE = "Chase-up: no contact since {last_contact_at}."
CHASE_WITHOUT_DATE = "Chase-up: no contact on record."

OPEN_STAGES = ("sourced", "submitted", "interviewing", "offered")


def _crawl(fetch_page) -> list[dict[str, Any]]:
    """Every row of one offset-paginated collection."""
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        envelope = fetch_page(offset)
        rows.extend(envelope.get("data") or [])
        step = int(envelope.get("limit") or 100)
        total = int(envelope.get("total") or 0)
        offset += step
        if offset >= total:
            return rows


def read_pipeline(client: PlacemintClient) -> list[dict[str, Any]]:
    """Every placement record Placemint currently holds, verbatim."""
    return _crawl(lambda offset: client.placement_page(offset=offset))


def last_contact_index(client: PlacemintClient,
                       cfg: Config) -> dict[str, dict[str, Any]]:
    """``placement_id`` -> the note record that is that placement's last contact."""
    index: dict[str, dict[str, Any]] = {}
    pages = _crawl(lambda offset: client.note_page(
        offset=offset, modified_since=cfg.stale_before))
    for note in pages:
        if note.get("is_deleted"):
            continue
        placement_id = str(note.get("placement_id") or "")
        if placement_id:
            index[placement_id] = note
    return index


def in_scope(placement: dict[str, Any]) -> bool:
    """Open pipeline only: not retired, and not in a stage that has closed."""
    return (not placement.get("is_deleted")) and placement.get("stage") in OPEN_STAGES


def run_contact_sweep(cfg: Config) -> dict[str, Any]:
    client = PlacemintClient(cfg)
    writer = ReportWriter(cfg.output_dir)

    pipeline = read_pipeline(client)
    contact = last_contact_index(client, cfg)

    rows: list[dict[str, Any]] = []
    for placement in pipeline:
        if not in_scope(placement):
            continue
        placement_id = str(placement.get("id"))
        note = contact.get(placement_id)
        last_contact_at = str(note.get("created_at")) if note else None
        stalled = last_contact_at is None or last_contact_at < cfg.stale_before

        filed_note_id = None
        if stalled:
            body = (CHASE_WITHOUT_DATE if last_contact_at is None
                    else CHASE_WITH_DATE.format(last_contact_at=last_contact_at))
            status, payload = client.create_note(
                placement_id, body, CHASE_AUTHOR,
                idempotency_key=f"chase:{placement_id}",
            )
            if status in (200, 201) and isinstance(payload, dict):
                filed_note_id = payload.get("id")

        rows.append({
            "placement_id": placement_id,
            "client_id": placement.get("client_id"),
            "stage": placement.get("stage"),
            "last_note_id": note.get("id") if note else None,
            "last_contact_at": last_contact_at,
            "stalled": stalled,
            "note_id": filed_note_id,
        })

    report = writer.write(rows)
    return {
        "scope_count": report["scope_count"],
        "stalled_count": report["stalled_count"],
        "fresh_count": report["fresh_count"],
        "pages_fetched": client.pages_fetched,
        "notes_created": client.notes_created,
        "token_mints": client.token_mints,
    }

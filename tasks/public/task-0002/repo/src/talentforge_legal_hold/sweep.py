"""Build the legal-hold export out of TalentForge.

Written against the written material we were handed — the vendor's guide in
``docs/`` and the outgoing contractor's handover note in
``input/HANDOVER-legal-hold.md``:

* ``docs/pagination.md`` — every list endpoint, including
  ``/candidates/{candidate_id}/notes``, is cursor paginated. Omit ``cursor`` on
  the first request, pass the returned one back, stop when it is ``null``.
* ``docs/entities.md`` — a candidate's ``created_at`` is epoch milliseconds
  (the documented quirk of this one entity) and its last-modified timestamp is
  ``updatedAt``, an ISO 8601 string. Job, application and note timestamps are
  ISO 8601 strings under ``created_at``/``modified_at``, so a note's
  ``created_at`` is carried across untouched.
* ``docs/index.md`` — soft deletes stay in list responses as
  ``is_deleted: true``; there is no tombstone endpoint. Nothing is filtered
  out of the crawl, because scope is the roster, not the state of the file.
* ``input/HANDOVER-legal-hold.md`` — the candidate list response carries the
  whole candidate record, so a row is built straight off the list page. The
  handover is explicit that re-reading a candidate through its own endpoint is
  wasted budget on a data plane that is metered per minute, and the March
  production was assembled this way.

Roster resolution is by normalised email against the whole tenant. Counsel's
rule is that a hold attaches to an address rather than to a record, so the
index maps an address to *every* candidate holding it, and each holder becomes
its own custodian row. A custodian with no notes still gets a row with an
empty ``notes`` list; an address no candidate carries is reported instead of
being dropped.
"""

from __future__ import annotations

from typing import Any

from talentforge_legal_hold.client import TalentForgeClient
from talentforge_legal_hold.config import Config
from talentforge_legal_hold.export import ExportWriter, iso_utc
from talentforge_legal_hold.roster import RosterRow, read_roster

MILLIS_PER_SECOND = 1000


def crawl(client: TalentForgeClient, path: str) -> list[dict[str, Any]]:
    """Every record the collection at ``path`` holds, in wire order."""
    rows: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        envelope = client.get(path, {"cursor": cursor})
        rows.extend(envelope.get("data") or [])
        # docs/pagination.md: the last page carries `cursor: null`.
        cursor = envelope.get("cursor")
        if cursor is None:
            return rows


def notes_for(client: TalentForgeClient, candidate_id: str) -> list[dict[str, Any]]:
    """Every note filed against one candidate."""
    return crawl(client, f"/candidates/{candidate_id}/notes")


def custodian_row(
    row: RosterRow, candidate: dict[str, Any], notes: list[dict[str, Any]]
) -> dict[str, Any]:
    """One export row: see ``export.py`` for the field-by-field contract."""
    return {
        "matter_ref": row.matter_ref,
        "roster_email": row.email,
        "candidate_id": candidate["id"],
        # HANDOVER: the list record is the whole candidate record.
        "given_name": candidate.get("given_name", ""),
        "family_name": candidate.get("family_name", ""),
        "phone": candidate.get("phone", ""),
        "pipeline_status": candidate.get("pipeline_status", ""),
        "is_deleted": bool(candidate.get("is_deleted")),
        # docs/entities.md: candidate.created_at is epoch millis, the one
        # documented exception to the platform's ISO 8601 timestamps.
        "created_at": iso_utc(int(candidate["created_at"]) / MILLIS_PER_SECOND),
        # docs/entities.md: candidate.updatedAt is already an ISO 8601 string.
        "updated_at": candidate.get("updatedAt"),
        # docs/entities.md: note timestamps are ISO 8601 strings.
        "notes": [
            {
                "note_id": note["id"],
                "author": note.get("author", ""),
                "body": note.get("body", ""),
                "created_at": note["created_at"],
            }
            for note in notes
        ],
    }


def index_by_email(candidates: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Tenant candidates grouped by normalised email.

    A list rather than a single record: a hold attaches to an address, so every
    candidate carrying a roster address is a custodian in their own right.
    """
    index: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        email = str(candidate.get("email") or "").strip().lower()
        if email:
            index.setdefault(email, []).append(candidate)
    return index


def run_export(cfg: Config) -> dict[str, Any]:
    client = TalentForgeClient(cfg)
    writer = ExportWriter(cfg.output_dir)
    roster = read_roster(cfg.roster_path)

    by_email = index_by_email(crawl(client, "/candidates"))

    custodians: list[dict[str, Any]] = []
    unmatched: list[str] = []
    for row in roster:
        matches = by_email.get(row.email) or []
        if not matches:
            unmatched.append(row.email)
            continue
        for candidate in matches:
            notes = notes_for(client, candidate["id"])
            custodians.append(custodian_row(row, candidate, notes))

    return writer.write(
        roster_row_count=len(roster),
        custodians=custodians,
        unmatched_roster_emails=unmatched,
    )

"""Output writer for the legal-hold export.

``legal_hold_export.json`` is the deliverable counsel receives. The shape,
with every value a placeholder::

    {"roster_row_count": 0,
     "custodian_count": 0,
     "note_count": 0,
     "custodians": [
       {"matter_ref": "XX-0000",
        "roster_email": "someone@example.invalid",
        "candidate_id": "<candidate id>",
        "given_name": "<string>",
        "family_name": "<string>",
        "phone": "<string>",
        "pipeline_status": "<enum>",
        "is_deleted": false,
        "created_at": "0000-00-00T00:00:00Z",
        "updated_at": "0000-00-00T00:00:00Z",
        "notes": [{"note_id": "<note id>",
                   "author": "<string>",
                   "body": "<string>",
                   "created_at": "0000-00-00T00:00:00Z"}]}],
     "unmatched_roster_emails": ["nobody@example.invalid"]}

``created_at`` and ``updated_at`` on a custodian are UTC ISO-8601 seconds
(``%Y-%m-%dT%H:%M:%SZ``); a note's ``created_at`` is the vendor's own value,
carried across untouched. ``iso_utc`` below is the one formatter — use it
rather than rolling another.

Ordering is handled here (custodians by ``candidate_id``, notes by
``note_id``), so callers may build the lists in whatever order the sweep
produced them.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

EXPORT_FILENAME = "legal_hold_export.json"


def iso_utc(epoch_seconds: float) -> str:
    """Seconds since the epoch -> ``YYYY-MM-DDTHH:MM:SSZ`` in UTC."""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch_seconds))


class ExportWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(
        self,
        *,
        roster_row_count: int,
        custodians: list[dict[str, Any]],
        unmatched_roster_emails: list[str],
    ) -> dict[str, Any]:
        ordered = []
        for custodian in sorted(custodians, key=lambda c: str(c.get("candidate_id"))):
            row = dict(custodian)
            row["notes"] = sorted(
                (dict(n) for n in row.get("notes") or []),
                key=lambda n: str(n.get("note_id")),
            )
            ordered.append(row)
        payload = {
            "roster_row_count": roster_row_count,
            "custodian_count": len(ordered),
            "note_count": sum(len(c["notes"]) for c in ordered),
            "custodians": ordered,
            "unmatched_roster_emails": sorted(set(unmatched_roster_emails)),
        }
        (self.output_dir / EXPORT_FILENAME).write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
        return payload

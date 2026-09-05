"""Artifacts and the cross-run ledger.

``attachment_report.json`` is one row per document in the export::

    {"document_count": 0,
     "delivered_count": 0,
     "quarantined_count": 0,
     "unresolved_count": 0,
     "documents": [{"doc_ref": "XX-0000",
                    "candidate_id": "cand_0000",
                    "outcome": "delivered",        # delivered|quarantined|unresolved
                    "reason": None,                # null unless the outcome needs one
                    "note_id": "note_00000",       # null when no note was created
                    "attachment_id": "att_00000",  # null when nothing is on the note
                    "attachment_state": "stored",  # stored|rejected|null
                    "attachment_reason": None,
                    "sha256": "0000...0000"}, ...]}

``quarantine.json`` is the subset the tenant has to deal with by hand -- every
document that did not end up delivered::

    {"count": 0,
     "documents": [{"doc_ref": "XX-0000",
                    "candidate_id": "cand_0000",
                    "outcome": "unresolved",
                    "reason": "<why>",
                    "note_id": None}, ...]}

Row order in either file is not significant. ``Ledger`` persists what a previous
pass already delivered so a re-run does not repeat work; it lives beside the
artifacts in ``OUTPUT_DIR``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPORT_FIELDS = (
    "doc_ref", "candidate_id", "outcome", "reason", "note_id",
    "attachment_id", "attachment_state", "attachment_reason", "sha256",
)


class Ledger:
    """Per-document record of what a previous pass already achieved."""

    def __init__(self, output_dir: Path) -> None:
        self.path = Path(output_dir) / "delivery_ledger.json"
        self.entries: dict[str, dict[str, Any]] = {}
        if self.path.is_file():
            try:
                self.entries = json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError:
                self.entries = {}

    def get(self, doc_ref: str) -> dict[str, Any] | None:
        return self.entries.get(doc_ref)

    def put(self, doc_ref: str, row: dict[str, Any]) -> None:
        self.entries[doc_ref] = dict(row)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.entries, indent=2, sort_keys=True),
                             encoding="utf-8")


class ScorecardStore:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        rows = [{field: row.get(field) for field in REPORT_FIELDS} for row in rows]
        rows.sort(key=lambda r: str(r.get("doc_ref")))

        report = {
            "document_count": len(rows),
            "delivered_count": sum(1 for r in rows if r.get("outcome") == "delivered"),
            "quarantined_count": sum(1 for r in rows if r.get("outcome") == "quarantined"),
            "unresolved_count": sum(1 for r in rows if r.get("outcome") == "unresolved"),
            "documents": rows,
        }
        (self.output_dir / "attachment_report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

        held = [
            {"doc_ref": r.get("doc_ref"), "candidate_id": r.get("candidate_id"),
             "outcome": r.get("outcome"), "reason": r.get("reason"),
             "note_id": r.get("note_id")}
            for r in rows if r.get("outcome") != "delivered"
        ]
        (self.output_dir / "quarantine.json").write_text(
            json.dumps({"count": len(held), "documents": held}, indent=2, sort_keys=True),
            encoding="utf-8")
        return report

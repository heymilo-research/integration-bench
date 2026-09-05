"""Apply Finance's fee corrections to Placemint.

One export row is one correction: it names a placement, the role title and fee
Finance want stored against it, who approved the change, and the reason the
client gave. Each correction becomes one `PATCH` on the placement and one note
carrying the reason verbatim, both under an `Idempotency-Key` derived from the
correction reference so a re-run replays instead of re-applying.

A row the reader cannot make sense of is `rejected`: nothing is written for it
and it is logged with the export line it came from, so Revenue Ops can go and
look. A row naming a placement Placemint has never issued comes back `404` and
is `unknown`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from placemint_fee_corrections.client import PlacemintClient
from placemint_fee_corrections.config import Config
from placemint_fee_corrections.report import CorrectionLogWriter

NOTE_AUTHOR = "corrections@meridian.test"


def read_corrections(path: Path) -> list[dict[str, Any]]:
    """The export's rows.

    The header names the fields; a row is those fields in the same order,
    separated by commas. A line that does not carry exactly as many fields as
    the header is not a row we can act on, so it is handed back marked bad and
    the caller rejects it.
    """
    rows: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8") as handle:
        header = handle.readline().rstrip("\n").split(",")
        for lineno, raw in enumerate(handle, start=2):
            text = raw.rstrip("\n")
            if not text:
                continue
            fields = text.split(",")
            if len(fields) != len(header):
                rows.append({"_source_line": lineno, "_parsed": False})
                continue
            row: dict[str, Any] = dict(zip(header, fields))
            row["_source_line"] = lineno
            row["_parsed"] = True
            rows.append(row)
    return rows


def run_corrections(cfg: Config) -> dict[str, Any]:
    client = PlacemintClient(cfg)
    writer = CorrectionLogWriter(cfg.output_dir)

    entries: list[dict[str, Any]] = []
    for row in read_corrections(cfg.corrections_file):
        entry: dict[str, Any] = {
            "correction_ref": None,
            "placement_id": None,
            "outcome": "rejected",
            "role_title": None,
            "fee_amount": None,
            "note_id": None,
            "source_line": row.get("_source_line"),
        }
        if not row.get("_parsed"):
            entries.append(entry)
            continue

        ref = row.get("correction_ref", "")
        placement_id = row.get("placement_id", "")
        entry["correction_ref"] = ref
        entry["placement_id"] = placement_id
        try:
            fee = float(row.get("fee_amount", ""))
        except (TypeError, ValueError):
            entries.append(entry)
            continue
        role_title = row.get("role_title", "")
        reason = row.get("reason", "")

        status, _body = client.update_placement(
            placement_id, {"role_title": role_title, "fee_amount": fee},
            idempotency_key=f"fee:{ref}",
        )
        if status == 404:
            entry["outcome"] = "unknown"
            entries.append(entry)
            continue
        if status != 200:
            entries.append(entry)
            continue

        note_status, note = client.create_note(
            placement_id, reason, NOTE_AUTHOR, idempotency_key=f"note:{ref}",
        )
        if note_status != 201:
            entries.append(entry)
            continue

        entry["outcome"] = "applied"
        entry["role_title"] = role_title
        entry["fee_amount"] = fee
        entry["note_id"] = note.get("id")
        entries.append(entry)

    log = writer.write(entries)
    return {
        "row_count": log["row_count"],
        "applied": log["applied_count"],
        "rejected": log["rejected_count"],
        "unknown": log["unknown_count"],
        "placements_updated": client.placements_updated,
        "notes_created": client.notes_created,
    }

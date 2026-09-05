"""Push the ATS's redeployments into Placemint.

A redeployment is a pair: the leaver closes on one placement and the joiner
opens on another, and the reason the desk recorded is filed as a note on the
placement the candidate moved to.

The order is the one Delivery Ops' runbook
(`docs/meridian-redeployment-runbook.md`) settled on: leaver, then joiner, then
the note, and if a call is refused we stop the row there so we never write half
a pair.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from placemint_movement_sync.client import PlacemintClient
from placemint_movement_sync.config import Config
from placemint_movement_sync.report import ReportWriter

NOTE_AUTHOR = "redeployments@meridian.test"


def read_redeployments(path: Path) -> list[dict[str, Any]]:
    """The export's rows, as strings, in file order."""
    rows: list[dict[str, Any]] = []
    with Path(path).open(encoding="utf-8", newline="") as fh:
        for line_no, raw in enumerate(csv.DictReader(fh), start=2):
            row = {key: (value or "").strip() for key, value in raw.items()}
            row["source_line"] = line_no
            rows.append(row)
    return rows


def apply_redeployment(client: PlacemintClient, row: dict[str, Any]) -> dict[str, Any]:
    """Settle one redeployment against Placemint and describe what happened."""
    ref = row["movement_ref"]
    entry: dict[str, Any] = {
        "movement_ref": ref,
        "from_placement_id": row["from_placement_id"],
        "to_placement_id": row["to_placement_id"],
        "outcome": "rejected",
        "from_stage": None,
        "to_stage": None,
        "to_fee_amount": None,
        "note_id": None,
        "source_line": row["source_line"],
    }

    # The leaver goes first. The runbook is explicit that the pair is one
    # movement to Placemint, so if the joiner is refused the leaver goes back
    # with it and there is nothing to undo here.
    status, _body = client.update_placement(
        row["from_placement_id"], {"stage": row["from_status"]},
        idempotency_key=f"rd:{ref}:from",
    )
    if status == 404:
        entry["outcome"] = "unknown"
        return entry
    if status != 200:
        return entry

    fields: dict[str, Any] = {"stage": row["to_status"]}
    try:
        fields["fee_amount"] = float(row["to_fee_amount"])
    except (TypeError, ValueError):
        return entry
    status, _body = client.update_placement(
        row["to_placement_id"], fields, idempotency_key=f"rd:{ref}:to",
    )
    if status == 404:
        entry["outcome"] = "unknown"
        return entry
    if status != 200:
        return entry

    status, note = client.create_note(
        row["to_placement_id"], row["reason"], NOTE_AUTHOR,
        idempotency_key=f"rd:{ref}:note",
    )
    if status == 404:
        entry["outcome"] = "unknown"
        return entry
    if status not in (200, 201):
        return entry

    entry["outcome"] = "applied"
    entry["from_stage"] = row["from_status"]
    entry["to_stage"] = row["to_status"]
    entry["to_fee_amount"] = fields["fee_amount"]
    entry["note_id"] = note.get("id") if isinstance(note, dict) else None
    return entry


def run_movement_sync(cfg: Config) -> dict[str, Any]:
    client = PlacemintClient(cfg)
    writer = ReportWriter(cfg.output_dir)

    rows = read_redeployments(cfg.redeployments_file)
    entries = [apply_redeployment(client, row) for row in rows]

    report = writer.write(entries)
    return {
        "row_count": report["row_count"],
        "applied_count": report["applied_count"],
        "rejected_count": report["rejected_count"],
        "unknown_count": report["unknown_count"],
        "placements_updated": client.placements_updated,
        "notes_created": client.notes_created,
        "token_mints": client.token_mints,
    }

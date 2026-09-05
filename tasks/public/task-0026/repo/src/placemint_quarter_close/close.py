"""Close the quarter's billable placements against Placemint.

Written from `docs/` and Revenue Ops' runbook:

- the two exports join on `invoice_ref`; the rate is on the header and the
  salary is on the line, and a line whose invoice is not `issued` is held;
- per the runbook, the `Idempotency-Key` is the invoice reference (and
  `note-<invoice_ref>` for the note that goes with it), so a close can never be
  applied to an invoice twice however often the job runs;
- per the runbook, the export no longer contains retired placements, so there
  is no reason to spend a request per line looking each one up. If Finance ever
  does send an id Placemint has never issued, the write comes back `404` and we
  record the line as unknown.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from placemint_quarter_close.client import PlacemintClient
from placemint_quarter_close.config import Config
from placemint_quarter_close.report import ReportWriter

NOTE_AUTHOR = "billing@meridian.test"

HIRED_REASONS = {"hired"}
CLOSED_STAGE = "fell_through"
HIRED_STAGE = "placed"


def read_exports(invoices_file: Path, lines_file: Path) -> tuple[list[dict[str, str]],
                                                                list[dict[str, str]]]:
    """The two exports' raw rows, as strings, in file order."""
    def rows(path: Path) -> list[dict[str, str]]:
        with Path(path).open(newline="", encoding="utf-8") as handle:
            return [
                {(k or "").strip(): (v or "").strip() for k, v in row.items()}
                for row in csv.DictReader(handle)
            ]
    return rows(invoices_file), rows(lines_file)


def join_lines(invoices: list[dict[str, str]],
               lines: list[dict[str, str]]) -> list[dict[str, Any]]:
    """One decided entry per placement line, in the order the lines arrived."""
    headers = {row.get("invoice_ref", ""): row for row in invoices}

    decided: list[dict[str, Any]] = []
    for row in lines:
        invoice_ref = row.get("invoice_ref", "")
        header = headers.get(invoice_ref)
        entry: dict[str, Any] = {
            "line_ref": row.get("line_ref", ""),
            "invoice_ref": invoice_ref,
            "placement_id": row.get("placement_id", ""),
            "outcome": None,
            "fee_amount": None,
            "stage": None,
            "note_id": None,
        }
        if header is None or header.get("status") != "issued":
            entry["outcome"] = "held"
            decided.append(entry)
            continue

        reason = row.get("close_reason", "")
        if reason in HIRED_REASONS:
            salary = float(row.get("base_salary") or 0)
            pct = float(header.get("fee_pct") or 0)
            entry["candidate_fee"] = round(salary * pct / 100.0, 2)
            entry["candidate_stage"] = HIRED_STAGE
        else:
            entry["candidate_fee"] = 0.0
            entry["candidate_stage"] = CLOSED_STAGE
        entry["candidate_note"] = (
            f"{invoice_ref} {reason} fee {entry['candidate_fee']:.2f}"
        )
        decided.append(entry)
    return decided


def run_quarter_close(cfg: Config) -> dict[str, Any]:
    client = PlacemintClient(cfg)
    writer = ReportWriter(cfg.output_dir)

    invoices, lines = read_exports(cfg.invoices_file, cfg.lines_file)
    decided = join_lines(invoices, lines)

    for entry in decided:
        if entry["outcome"] == "held":
            continue
        placement_id = entry["placement_id"]
        fee = entry.pop("candidate_fee")
        stage = entry.pop("candidate_stage")
        note_body = entry.pop("candidate_note")

        # Revenue Ops' standing rule: the invoice is the unit that gets billed,
        # so the invoice reference is the key.
        status, body = client.update_placement(
            placement_id, {"stage": stage, "fee_amount": fee},
            idempotency_key=entry["invoice_ref"],
        )
        if status == 404:
            entry["outcome"] = "unknown"
            continue
        if status != 200:
            raise RuntimeError(f"{entry['line_ref']}: PATCH {placement_id} -> {status} {body}")

        note_status, note = client.create_note(
            placement_id, note_body, NOTE_AUTHOR,
            idempotency_key=f"note-{entry['invoice_ref']}",
        )
        if note_status == 404:
            entry["outcome"] = "unknown"
            continue
        if note_status != 201:
            raise RuntimeError(
                f"{entry['line_ref']}: POST note on {placement_id} -> {note_status} {note}"
            )

        entry["outcome"] = "applied"
        entry["fee_amount"] = fee
        entry["stage"] = stage
        entry["note_id"] = note.get("id")

    for entry in decided:
        entry.pop("candidate_fee", None)
        entry.pop("candidate_stage", None)
        entry.pop("candidate_note", None)

    report = writer.write(decided)
    return {
        "line_count": report["line_count"],
        "applied": report["applied_count"],
        "held": report["held_count"],
        "retired": report["retired_count"],
        "unknown": report["unknown_count"],
        "placements_updated": client.placements_updated,
        "notes_created": client.notes_created,
    }

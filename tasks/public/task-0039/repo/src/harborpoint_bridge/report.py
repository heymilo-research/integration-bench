"""Render the payroll artifacts described in ``PROBLEM.md``."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

NOTE_AUTHOR = "payroll-bridge@harborpoint.test"

REPORT_COLUMNS = [
    "punch_ref", "shift_id", "worker_id", "venue_timezone", "payroll_date", "minutes",
]


def idempotency_key_for(punch_ref: str) -> str:
    """The write key Harbor Point uses for a punch's payroll-split note."""
    return f"hp-{punch_ref}"


def note_body(punch_ref: str, shift_id: str, venue_timezone: str,
              days: list[dict[str, Any]]) -> str:
    """The note text the scheduling team reads. One line, days in order."""
    spread = ",".join(f"{d['payroll_date']}={d['minutes']}" for d in days)
    return (f"Payroll split | punch {punch_ref} | shift {shift_id} | "
            f"{venue_timezone} | {spread}")


class ReportWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, punches: list[dict[str, Any]], skipped: list[dict[str, Any]],
              notes: list[dict[str, Any]]) -> dict[str, Any]:
        rows: list[dict[str, Any]] = []
        for punch in punches:
            for day in punch["days"]:
                rows.append({
                    "punch_ref": punch["punch_ref"],
                    "shift_id": punch["shift_id"],
                    "worker_id": punch["worker_id"],
                    "venue_timezone": punch["venue_timezone"],
                    "payroll_date": day["payroll_date"],
                    "minutes": day["minutes"],
                })

        report_path = self.output_dir / "import_report.csv"
        with report_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=REPORT_COLUMNS)
            writer.writeheader()
            writer.writerows(rows)

        payload = {
            "punch_count": len(punches) + len(skipped),
            "bridged_count": len(punches),
            "unbridgeable_count": len(skipped),
            "split_line_count": len(rows),
            "midnight_split_count": sum(1 for p in punches if len(p["days"]) > 1),
            "total_minutes": sum(int(p["minutes"]) for p in punches),
            "notes_posted": len(notes),
            "punches": punches,
            "unbridgeable": skipped,
        }
        (self.output_dir / "result.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        (self.output_dir / "writeback_log.json").write_text(
            json.dumps({"notes": notes}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8")
        return payload

"""The three output artifacts.

The shapes below are the output contract from the ticket. Nothing in this
module decides anything; it serialises whatever the reconciler handed it.

    output/result.json          cycle number, outcome counts, stored positions
    output/import_report.csv    one row per crosswalk line, in crosswalk order
    output/writeback_log.json   one entry per write this cycle sent to Placemint
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

OUTCOMES = ("pushed", "inbound", "in_sync", "retired", "unlinked")

REPORT_COLUMNS = (
    "application_id",
    "placement_id",
    "outcome",
    "ats_stage",
    "target_stage",
    "resulting_stage",
)


@dataclass
class CycleRow:
    application_id: str
    placement_id: str
    outcome: str
    ats_stage: str = ""
    target_stage: str = ""
    resulting_stage: str = ""


@dataclass
class WriteRecord:
    placement_id: str
    stage: str
    status: int
    idempotency_key: str


@dataclass
class CycleResult:
    cycle: int
    rows: list[CycleRow] = field(default_factory=list)
    writes: list[WriteRecord] = field(default_factory=list)
    ats_watermark: str = ""
    marketplace_watermark: str = ""


def write_artifacts(output_dir: Path, result: CycleResult) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    counts = {name: 0 for name in OUTCOMES}
    for row in result.rows:
        if row.outcome in counts:
            counts[row.outcome] += 1

    (output_dir / "result.json").write_text(
        json.dumps(
            {
                "cycle": result.cycle,
                "links": len(result.rows),
                "counts": counts,
                "ats_watermark": result.ats_watermark,
                "marketplace_watermark": result.marketplace_watermark,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    with (output_dir / "import_report.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(REPORT_COLUMNS)
        for row in result.rows:
            writer.writerow(
                [
                    row.application_id,
                    row.placement_id,
                    row.outcome,
                    row.ats_stage,
                    row.target_stage,
                    row.resulting_stage,
                ]
            )

    (output_dir / "writeback_log.json").write_text(
        json.dumps(
            {
                "cycle": result.cycle,
                "writes": [
                    {
                        "placement_id": w.placement_id,
                        "stage": w.stage,
                        "status": w.status,
                        "idempotency_key": w.idempotency_key,
                    }
                    for w in result.writes
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

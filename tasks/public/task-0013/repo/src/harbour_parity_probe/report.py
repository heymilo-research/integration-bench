"""The three output artifacts.

Nothing here decides anything; it serialises whatever the parity pass handed it.

    output/import_report.csv   one row per divergence, sorted by entity then id
    output/result.json         the run's counts and the repaired census
    output/events.json         the webhook events the listener accepted
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path

DIVERGENCES = ("add", "update", "remove", "drop")

REPORT_COLUMNS = ("entity", "record_id", "divergence", "mirror_value", "vendor_value")


@dataclass
class Divergence:
    entity: str
    record_id: str
    divergence: str
    mirror_value: str = ""
    vendor_value: str = ""


@dataclass
class ParityResult:
    rows: list[Divergence] = field(default_factory=list)
    census: dict[str, int] = field(default_factory=dict)
    synced_through: str = ""
    events: list[dict] = field(default_factory=list)


def write_artifacts(output_dir: Path, result: ParityResult) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = sorted(result.rows, key=lambda r: (r.entity, r.record_id))
    counts = {name: 0 for name in DIVERGENCES}
    for row in rows:
        if row.divergence in counts:
            counts[row.divergence] += 1

    with (output_dir / "import_report.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(REPORT_COLUMNS)
        for row in rows:
            writer.writerow(
                [row.entity, row.record_id, row.divergence, row.mirror_value, row.vendor_value]
            )

    (output_dir / "result.json").write_text(
        json.dumps(
            {
                "source": "recruitos",
                "snapshot_synced_through": result.synced_through,
                "divergences": counts,
                "census": result.census,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    (output_dir / "events.json").write_text(
        json.dumps({"applied": result.events}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

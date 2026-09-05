"""Turning the roster into the night's census.

One census line per crew member the tenant holds, plus the headline totals and
the two breakdowns the capacity model reads: one per role, one per status.
"""

from __future__ import annotations

from typing import Any

from fenmarsh_census.client import CrewCallClient
from fenmarsh_census.config import Config
from fenmarsh_census.report import ReportWriter
from fenmarsh_census.sweep import sweep_roster


def census_lines(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One census line per crew member the roster holds.

    A crew member CrewCall has taken off the books is still on the roster
    listing, carrying its deletion flag; the census carries them as `removed`
    rather than dropping them, because the capacity model reports leavers.
    """
    return [
        {
            "worker_id": str(record.get("id") or ""),
            "role": str(record.get("role") or ""),
            "status": str(record.get("status") or ""),
            "standing": "removed" if record.get("is_deleted") else "active",
        }
        for record in records
    ]


def summarise(lines: list[dict[str, Any]], *, pages_read: int) -> dict[str, Any]:
    by_role: dict[str, dict[str, Any]] = {}
    for line in lines:
        bucket = by_role.setdefault(
            line["role"], {"role": line["role"], "active": 0, "removed": 0}
        )
        bucket[line["standing"]] += 1

    by_status: dict[str, dict[str, Any]] = {}
    for line in lines:
        if line["standing"] != "active":
            continue
        bucket = by_status.setdefault(
            line["status"], {"status": line["status"], "headcount": 0}
        )
        bucket["headcount"] += 1

    return {
        "roster_rows": len(lines),
        "active_headcount": sum(1 for l in lines if l["standing"] == "active"),
        "removed_headcount": sum(1 for l in lines if l["standing"] == "removed"),
        "by_role": [by_role[k] for k in sorted(by_role)],
        "by_status": [by_status[k] for k in sorted(by_status)],
        "pages_read": pages_read,
    }


def run_census(cfg: Config) -> dict[str, Any]:
    client = CrewCallClient(cfg)
    writer = ReportWriter(cfg.output_dir)

    records = sweep_roster(client)
    lines = census_lines(records)
    summary = summarise(lines, pages_read=client.pages_read)
    writer.write(lines, summary)
    return summary

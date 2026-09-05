"""The weekly crew top-up.

Reads the agency's placement file, collapses it to one entry per crew member,
sweeps the CrewCall roster, signs up the crew members CrewCall does not hold,
and writes the two artifacts.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from ironvale_topup.client import CrewCallClient
from ironvale_topup.config import Config
from ironvale_topup.report import ReportWriter
from ironvale_topup.roster import build_index, resolve, sweep_roster


def load_placements(path: Path) -> list[dict[str, str]]:
    """The agency's file, raw."""
    with Path(path).open(encoding="utf-8", newline="") as fh:
        return [dict(row) for row in csv.DictReader(fh)]


def person_key(crew_email: str) -> str:
    return str(crew_email or "").strip().lower()


def collapse(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """One entry per crew member the file places.

    The file is one row per placement, so somebody placed on three nights is in
    it three times.
    """
    people: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = person_key(row.get("crew_email"))
        entry = people.setdefault(key, {
            "person_key": key,
            "crew_email": key,
            "crew_name": str(row.get("crew_name") or "").strip(),
            "crew_phone": str(row.get("crew_phone") or "").strip(),
            "placement_refs": [],
        })
        entry["placement_refs"].append(str(row.get("placement_ref") or ""))
    return [people[k] for k in sorted(people)]


def signup_payload(entry: dict[str, Any]) -> dict[str, Any]:
    """A create body for a crew member CrewCall does not hold."""
    parts = [p for p in str(entry.get("crew_name") or "").split() if p]
    first = parts[0] if parts else entry["crew_email"].split("@", 1)[0]
    last = " ".join(parts[1:]) if len(parts) > 1 else ""
    return {
        "first_name": first,
        "last_name": last or first,
        "email": entry["crew_email"],
        "phone": entry.get("crew_phone") or "",
    }


def run_topup(cfg: Config) -> dict[str, Any]:
    client = CrewCallClient(cfg)
    writer = ReportWriter(cfg.output_dir)

    rows = load_placements(cfg.input_file)
    people = collapse(rows)

    roster = sweep_roster(client)
    index = build_index(roster)

    for entry in people:
        worker = resolve(entry["crew_email"], index)
        if worker is None:
            worker = client.create_worker(signup_payload(entry))
            entry["outcome"] = "created"
        else:
            entry["outcome"] = "matched"
        entry["worker_id"] = str(worker.get("id") or "")

    by_key = {p["person_key"]: p for p in people}
    placement_rows = []
    for row in rows:
        entry = by_key.get(person_key(row.get("crew_email")), {})
        placement_rows.append({
            "placement_ref": str(row.get("placement_ref") or ""),
            "shift_date": str(row.get("shift_date") or ""),
            "crew_email": person_key(row.get("crew_email")),
            "worker_id": entry.get("worker_id", ""),
            "outcome": entry.get("outcome", ""),
        })

    payload = writer.write(placement_rows, people, roster_rows_seen=len(roster))
    return {
        "row_count": payload["row_count"],
        "person_count": payload["person_count"],
        "matched_count": payload["matched_count"],
        "created_count": payload["created_count"],
        "roster_rows_seen": payload["roster_rows_seen"],
        "pages_fetched": client.pages_fetched,
        "pages_unavailable": client.pages_unavailable,
        "workers_created": client.workers_created,
    }

"""The nightly refresh of Nordhavn's Postgres mirror of Rosterly.

This is the job that has run since the mirror went in. It reads the crew
roster and the mirror inventory, walks Rosterly's collections, keeps the
records that belong to Nordhavn's own crew, and rewrites one mirror row per
record in the mirror's storage shape.

The rules Nordhavn work to are in
``docs/nordhavn-mirror-migration-spec.md``; Rosterly's own documentation is in
``docs/``.

``client.py``, ``config.py``, ``inventory.py`` and ``report.py`` carry the
plumbing.
"""

from __future__ import annotations

from typing import Any

from nordhavn_mirror_port import inventory
from nordhavn_mirror_port.client import RosterlyClient
from nordhavn_mirror_port.config import Config
from nordhavn_mirror_port.report import ReportWriter

# The collections the mirror covers, in the order the loader wants them.
COLLECTIONS = [("worker", "workers"), ("shift", "shifts"), ("interview", "interviews")]

# The venue clock register, section 2 of the migration spec.
VENUE_OFFSETS = {
    "America/Los_Angeles": "-08:00",
    "America/New_York": "-05:00",
    "America/Sao_Paulo": "-03:00",
    "Asia/Kolkata": "+05:30",
    "Asia/Tokyo": "+09:00",
    "Australia/Sydney": "+11:00",
    "Europe/Berlin": "+01:00",
    "Pacific/Chatham": "+13:45",
}

# The group's home clock, which the spec says the crew-record stamps are on.
HOME_ZONE = "Europe/Copenhagen"
HOME_ZONE_OFFSET = "+01:00"


def wall_clock(value: str) -> str:
    """The local wall clock Rosterly reported, with any zone suffix removed."""
    value = str(value).strip()
    return value.rsplit(" ", 1)[0] if " " in value else value


def zone_of(entity: str, record: dict[str, Any]) -> str:
    """Which clock this record's stamps are on."""
    if entity == "worker":
        return HOME_ZONE
    return str(record.get("timezone") or HOME_ZONE)


def offset_table(zones: list[str]) -> dict[str, str]:
    """The offset the run applied to each clock it saw."""
    table: dict[str, str] = {}
    for zone in zones:
        table[zone] = HOME_ZONE_OFFSET if zone == HOME_ZONE else VENUE_OFFSETS.get(zone, "")
    return table


def in_scope(entity: str, record: dict[str, Any], crew: set[str]) -> bool:
    """Nordhavn owns the crew on the roster, and the rota rows that name them."""
    if entity == "worker":
        return str(record.get("id")) in crew
    return str(record.get("worker_id")) in crew


def run_migration(cfg: Config) -> dict[str, Any]:
    crew = set(inventory.read_crew(cfg.crew_roster_file))
    held = inventory.read_inventory(cfg.mirror_inventory_file)
    if not crew:
        raise RuntimeError("the crew roster is empty; nothing is in scope")

    row_ids = {(str(r["entity"]), str(r["record_id"])): str(r["mirror_row_id"])
               for r in held}
    minted = len(held)

    client = RosterlyClient(cfg)
    fetched: list[tuple[str, dict[str, Any]]] = []
    for entity, plural in COLLECTIONS:
        for record in client.collection(plural):
            if in_scope(entity, record, crew):
                fetched.append((entity, record))

    rows: list[dict[str, Any]] = []
    for entity, record in fetched:
        key = (entity, str(record["id"]))
        if key not in row_ids:
            minted += 1
            row_ids[key] = f"nh-{minted:05d}"
        rows.append({
            "mirror_row_id": row_ids[key],
            "entity": entity,
            "record_id": str(record["id"]),
            "stored_zone": zone_of(entity, record),
            "stored_local": wall_clock(record["updated_at"]),
        })
    rows.sort(key=lambda row: (row["entity"], row["record_id"]))

    summary = {
        "mirror_row_count": len(rows),
        "zone_offsets": offset_table(sorted({row["stored_zone"] for row in rows})),
    }
    ReportWriter(cfg.output_dir).write(rows, summary)
    return {
        "mirror_row_count": summary["mirror_row_count"],
        "pages_fetched": client.pages_fetched,
    }

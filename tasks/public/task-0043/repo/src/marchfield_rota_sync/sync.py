"""One incremental pass over Rosterly, into the warehouse's change ledger.

This is the module that has run nightly since the spring. It reads the
watermark the previous pass left behind, asks each collection what has changed
since then, appends one ledger row per change, and leaves a fresh watermark for
tomorrow. The rules Marchfield work to are in
``docs/marchfield-rota-sync-runbook.md``; Rosterly's own documentation is in
``docs/``.

"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from marchfield_rota_sync.client import RosterlyClient
from marchfield_rota_sync.config import Config
from marchfield_rota_sync.report import ReportWriter
from marchfield_rota_sync import store

# The collections the warehouse mirrors, in the order the loader wants them.
COLLECTIONS = [("worker", "workers"), ("shift", "shifts"), ("interview", "interviews")]

# The safety margin the runbook asks for -- see "Why we rewind an hour".
WATERMARK_REWIND = timedelta(hours=1)


def parse_wire(value: str) -> datetime:
    """Either Rosterly wire format as a UTC instant.

    A bare stamp is UTC. A stamp with a trailing IANA zone name is that zone's
    local wall-clock, so it is localized there before converting.
    """
    value = str(value).strip()
    if " " in value:
        iso_part, zone_name = value.rsplit(" ", 1)
        local = datetime.strptime(iso_part, "%Y-%m-%dT%H:%M:%S").replace(
            tzinfo=ZoneInfo(zone_name))
        return local.astimezone(timezone.utc)
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def utc_column(value: str) -> str:
    """The ledger's canonical instant column."""
    return parse_wire(value).strftime("%Y-%m-%dT%H:%M:%SZ")


def watermark_format(moment: datetime) -> str:
    """The bare-naive UTC shape Rosterly's ``modified_since`` takes."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def change_for(record: dict[str, Any]) -> str:
    """What this record's change is, from the warehouse loader's point of view.

    Rosterly takes a removed worker or a cancelled shift out of the feed (see
    the runbook), so everything the feed hands back is a record that is still
    on the rota.
    """
    return "upsert"


def next_watermark(records: list[dict[str, Any]], previous: str) -> str:
    """Where tomorrow's pass should start from.

    The newest change this pass saw, rewound by the runbook's safety margin. A
    pass that saw nothing leaves the watermark where it was.
    """
    if not records:
        return previous
    newest = max(parse_wire(record["updated_at"]) for record in records)
    return watermark_format(newest - WATERMARK_REWIND)


def run_sync(cfg: Config) -> dict[str, Any]:
    if not cfg.sync_since:
        raise RuntimeError("SYNC_SINCE is not set; the first pass has no starting point")

    client = RosterlyClient(cfg)
    writer = ReportWriter(cfg.output_dir)
    state = store.load(cfg.state_dir)

    watermark_in = state["watermark"] or cfg.sync_since
    run_no = len(state["runs"]) + 1

    fetched: list[tuple[str, dict[str, Any]]] = []
    for entity, collection in COLLECTIONS:
        for record in client.changed_since(collection, watermark_in):
            fetched.append((entity, record))

    rows: list[dict[str, Any]] = []
    for entity, record in fetched:
        rows.append({
            "run": run_no,
            "entity": entity,
            "record_id": str(record["id"]),
            "change": change_for(record),
            "updated_at_utc": utc_column(record["updated_at"]),
        })
    rows.sort(key=lambda row: (row["updated_at_utc"], row["record_id"]))

    state["ledger"].extend(rows)
    state["runs"].append({
        "run": run_no,
        "watermark_in": watermark_in,
        "watermark_out": next_watermark([r for _e, r in fetched], watermark_in),
        "emitted": sorted(row["record_id"] for row in rows),
        "removed": sorted(row["record_id"] for row in rows if row["change"] == "delete"),
        "upserts": sum(1 for row in rows if row["change"] == "upsert"),
        "deletes": sum(1 for row in rows if row["change"] == "delete"),
    })
    state["watermark"] = state["runs"][-1]["watermark_out"]
    store.save(cfg.state_dir, state)

    payload = writer.write(state["ledger"], state["runs"])
    return {
        "run": run_no,
        "run_count": payload["run_count"],
        "ledger_row_count": payload["ledger_row_count"],
        "distinct_record_count": payload["distinct_record_count"],
        "rows_this_pass": len(rows),
        "watermark_out": state["watermark"],
        "pages_fetched": client.pages_fetched,
    }

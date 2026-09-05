"""Migration orchestration. See ``PROBLEM.md``."""

from __future__ import annotations

from typing import Any

from staffline_to_bullpen_migrate import store
from staffline_to_bullpen_migrate.bullpen_client import BullpenClient
from staffline_to_bullpen_migrate.config import Config
from staffline_to_bullpen_migrate.staffline_reader import read_baseline

TOMBSTONES_CACHE = ".staffline_tombstones.json"

ENTITY_FILES = {
    "candidate": "candidates.json",
    "job": "jobs.json",
    "application": "applications.json",
}
BULLPEN_KIND = {
    "candidate": "candidates",
    "job": "jobs",
    "application": "applications",
}


def legacy_id(entity: str, staffline_id: str) -> str:
    return f"legacy:{entity}:{staffline_id}"


def _staffline_row(entity: str, record: dict[str, Any], is_deleted: bool) -> dict[str, Any]:
    rec = dict(record)
    sid = rec.pop("id")
    updated_at = rec.get("mod_ts", rec.get("crt_ts", 0))
    return {
        "source_id": sid,
        "data": rec,
        "is_deleted": is_deleted,
        "updated_at": updated_at,
    }


def run_baseline(config: Config) -> None:
    """Read StaffLine one last time and write staffline-shaped snapshots."""
    baseline = read_baseline(config)

    for entity, filename in ENTITY_FILES.items():
        rows = [_staffline_row(entity, rec, is_deleted=False) for rec in baseline.active.get(entity, [])]
        out_path = config.output_dir / filename
        store.write_store(out_path, sorted(rows, key=lambda r: r["source_id"]))

    tombstones = [
        {"entity": t.entity, "source_id": t.source_id, "deleted_at": t.deleted_at}
        for t in baseline.tombstones
    ]
    store.write_json(config.output_dir / TOMBSTONES_CACHE, tombstones)


def run_migrate(config: Config) -> None:
    """Cutover backfill from Bullpen v2."""
    client = BullpenClient(config.bullpen_base_url, config.bp_client_id, config.bp_client_secret)

    for entity, filename in ENTITY_FILES.items():
        bullpen_rows = [
            {
                "source_id": rec["source_id"],
                "data": {k: v for k, v in rec.items() if k not in ("id", "source_id")},
                "is_deleted": rec["is_deleted"],
                "updated_at": rec["modified_at"],
            }
            for rec in client.fetch_all(BULLPEN_KIND[entity])
        ]
        out_path = config.output_dir / filename
        store.write_store(out_path, sorted(bullpen_rows, key=lambda r: r["source_id"]))

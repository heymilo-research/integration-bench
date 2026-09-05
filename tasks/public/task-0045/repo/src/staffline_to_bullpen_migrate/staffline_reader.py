"""Full-sweep StaffLine reader. See ``PROBLEM.md``."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from staffline_to_bullpen_migrate.config import Config
from staffline_to_bullpen_migrate.staffline_client import StafflineClient, StafflineError

CANDIDATES_PATH = "/svc/candidates"
JOBS_PATH = "/svc/jobs"
APPLICATIONS_PATH = "/svc/applications"
NOTES_PATH = "/svc/notes"
TOMBSTONES_PATH = "/svc/tombstones"
PAGE_SIZE = 50

ENTITY_PATHS = {
    "candidate": CANDIDATES_PATH,
    "job": JOBS_PATH,
    "application": APPLICATIONS_PATH,
    "note": NOTES_PATH,
}


@dataclass
class Tombstone:
    entity: str
    source_id: str
    deleted_at: int


@dataclass
class StafflineBaseline:
    """Every active record (by entity kind) plus every tombstone StaffLine
    has ever recorded, as of the moment this baseline was read."""

    active: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    tombstones: list[Tombstone] = field(default_factory=list)


def _drain(client: StafflineClient, path: str, extra: dict[str, Any]) -> list[dict]:
    """Page a StaffLine list endpoint to exhaustion, raising on any non-200."""
    out: list[dict] = []
    start = 0
    while True:
        params = {"start": start, "count": PAGE_SIZE, **extra}
        status, body = client.get(path, params)
        if status != 200 or not isinstance(body, dict):
            raise StafflineError(status, path, body)
        out.extend(body.get("rows", []))
        if not body.get("more"):
            return out
        start += PAGE_SIZE


def read_baseline(config: Config) -> StafflineBaseline:
    """Read the full StaffLine baseline."""
    client = StafflineClient(config)
    baseline = StafflineBaseline()

    baseline.active["candidate"] = _drain(client, CANDIDATES_PATH, {})
    baseline.active["job"] = _drain(client, JOBS_PATH, {})
    baseline.active["application"] = _drain(
        client, APPLICATIONS_PATH, {"include_stage": 1}
    )
    baseline.active["note"] = _drain(client, NOTES_PATH, {})

    for row in _drain(client, TOMBSTONES_PATH, {"since": 0}):
        baseline.tombstones.append(
            Tombstone(entity=row["entity"], source_id=row["id"], deleted_at=int(row["deleted_at"]))
        )
    return baseline

"""StaffLine sync. See ``PROBLEM.md``."""

from __future__ import annotations

from staffline_sync.client import StafflineClient
from staffline_sync.config import Config
from staffline_sync import store

CANDIDATES_PATH = "/svc/candidates"
JOBS_PATH = "/svc/jobs"
APPLICATIONS_PATH = "/svc/applications"
NOTES_PATH = "/svc/notes"
TOMBSTONES_PATH = "/svc/tombstones"

ENTITY_PATHS: dict[str, str] = {
    "candidate": CANDIDATES_PATH,
    "job": JOBS_PATH,
    "application": APPLICATIONS_PATH,
    "note": NOTES_PATH,
}

OUTPUT_FILENAMES: dict[str, str] = {
    "candidate": "candidates.json",
    "job": "jobs.json",
    "application": "applications.json",
    "note": "notes.json",
}


def sync_entity(client: StafflineClient, conn, entity: str, *, incremental: bool) -> None:
    """Fetch one entity kind and upsert into the canonical store."""
    raise NotImplementedError


def sweep_tombstones(client: StafflineClient, conn) -> None:
    """Apply upstream deletions to the canonical store."""
    raise NotImplementedError


def run(config: Config, *, incremental: bool) -> None:
    conn = store.connect(config.database_url)
    store.ensure_schema(conn)
    client = StafflineClient(config)

    for entity in ENTITY_PATHS:
        sync_entity(client, conn, entity, incremental=incremental)
    sweep_tombstones(client, conn)

    store.dump_all(conn, config.output_dir, OUTPUT_FILENAMES)

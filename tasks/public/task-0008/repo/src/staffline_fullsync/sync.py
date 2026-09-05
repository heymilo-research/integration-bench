"""StaffLine polling sync. See ``PROBLEM.md``."""

from __future__ import annotations

from .client import StafflineClient
from .config import Config
from .store import Store

CANDIDATES_PATH = "/svc/candidates"
APPLICATIONS_PATH = "/svc/applications"
TOMBSTONES_PATH = "/svc/tombstones"
PAGE_SIZE = 50  # StaffLine's documented default/max `count`

# Canonical table for each tombstone-feed entity name.
_ENTITY_TABLE = {
    "candidate": "candidates",
    "job": "jobs",
    "application": "applications",
    "note": "notes",
}


def sync(config: Config) -> None:
    """Run one full sync pass into the canonical store under ``output_dir``."""
    client = StafflineClient(config)
    store = Store(config.output_dir)

    _sync_candidates(client, store)
    _sync_applications(client, store)
    _sweep_tombstones(client, store)


def _drain(client: StafflineClient, path: str, extra: dict[str, object]) -> list[dict]:
    """Collect all rows from a StaffLine list endpoint."""
    raise NotImplementedError


def _sync_candidates(client: StafflineClient, store: Store) -> None:
    rows = store.load("candidates")
    for record in _drain(client, CANDIDATES_PATH, {}):
        store.upsert(
            rows,
            record["id"],
            {k: v for k, v in record.items() if k != "id"},
            int(record.get("mod_ts", 0)),
        )
    store.write("candidates", rows)


def _sync_applications(client: StafflineClient, store: Store) -> None:
    rows = store.load("applications")
    for record in _drain(client, APPLICATIONS_PATH, {}):
        store.upsert(
            rows,
            record["id"],
            {k: v for k, v in record.items() if k != "id"},
            int(record.get("mod_ts", 0)),
        )
    store.write("applications", rows)


def _sweep_tombstones(client: StafflineClient, store: Store) -> None:
    """Reconcile upstream deletions via the tombstone feed."""
    raise NotImplementedError

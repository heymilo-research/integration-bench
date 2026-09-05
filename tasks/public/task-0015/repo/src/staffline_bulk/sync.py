"""Bulk import push loop. See ``PROBLEM.md``."""

from __future__ import annotations

from typing import Any

import sqlite3

from staffline_bulk import store
from staffline_bulk.client import StafflineClient
from staffline_bulk.config import Config


# ---------------------------------------------------------------------------
# Plumbing (provided)
# ---------------------------------------------------------------------------

def read_batch(input_file) -> list[dict[str, Any]]:
    """Load the staged candidate batch: a list of items, each with a stable
    ``client_ref`` plus the candidate's fields."""
    import json
    from pathlib import Path

    data = json.loads(Path(input_file).read_text(encoding="utf-8"))
    return list(data.get("items", []))


def write_result(output_dir, rows: list[dict[str, Any]]) -> None:
    """Write the durable store's contents to ``output_dir/bulk_result.json``.

    ``rows`` is the shape :func:`staffline_bulk.store.all_results` returns
    (``{"client_ref", "created", "candidate_id"}``); this reshapes it to the
    ticket's output contract (``id`` instead of ``candidate_id``, sorted).
    """
    import json
    from pathlib import Path

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    items = sorted(
        (
            {"client_ref": r["client_ref"], "created": bool(r["created"]), "id": r["candidate_id"]}
            for r in rows
        ),
        key=lambda r: r["client_ref"],
    )
    out = output_dir / "bulk_result.json"
    with out.open("w", encoding="utf-8") as fh:
        json.dump({"items": items}, fh, indent=2, sort_keys=True)


# ---------------------------------------------------------------------------
# Push loop
# ---------------------------------------------------------------------------

def push_pending(
    client: StafflineClient, conn: sqlite3.Connection, batch_items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Import pending items and durably record per-item outcomes."""
    status, body = client.bulk_create(batch_items)
    results = body.get("results", []) if isinstance(body, dict) else []

    for result in results:
        client_ref = result.get("client_ref")
        if client_ref is None:
            continue
        if result.get("status") == 201:
            store.upsert_result(conn, client_ref=client_ref, created=True, candidate_id=result.get("id"))
        else:
            store.upsert_result(conn, client_ref=client_ref, created=False, candidate_id=None)

    return store.all_results(conn)


def run_push(cfg: Config, conn: sqlite3.Connection) -> list[dict[str, Any]]:
    client = StafflineClient(cfg)
    batch = read_batch(cfg.input_file)
    result = push_pending(client, conn, batch)
    write_result(cfg.output_dir, result)
    return result

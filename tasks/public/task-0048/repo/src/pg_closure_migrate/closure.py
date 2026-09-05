"""The Brackett -> Paygrade closure cutover.

The archive is at ``input/brackett_closure_archive.csv``, relative to the
package root. ``docs/brackett-paygrade-cutover-note.md`` is Brackett's own
cutover note for the migration; it describes how the archive lines up with
Paygrade and what each `record_kind` needs. The Paygrade wire contract is in
``docs/``.

Two reads are enough for this pass, per that note:

* ``listEmployees`` for the worker population (the RPC surface hard-requires a
  ``company_id`` on that method -- the error body names it);
* ``listTombstones`` swept from the epoch, because a worker that is simply
  absent from a collection is indistinguishable from one Paygrade never held,
  and the feed is the only thing that separates the two.

The note is explicit that the archive's own ``open_placements`` is Paygrade's
reconciled count, that a placement closes with its worker, and that pay periods
are never removed -- so none of those three needs a Paygrade lookup of its own.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pg_closure_migrate.client import PaygradeClient
from pg_closure_migrate.config import Config
from pg_closure_migrate.store import ClosureStore

ARCHIVE_PATH = Path(__file__).resolve().parents[2] / "input" / "brackett_closure_archive.csv"

# The five outcomes the ticket names.
CLOSED_HERE = "closed_here"
BLOCKED = "blocked"
ALREADY_CLOSED = "already_closed"
NO_CLOSE_SURFACE = "no_close_surface"
NOT_IN_PAYGRADE = "not_in_paygrade"

TERMINATED = "terminated"

_COMPANY_ID = "brackett-cutover"
_SWEEP_FROM_MS = 0


@dataclass
class Decision:
    """What this pass concluded about one archive row."""

    outcome: str
    removed_at: int | None = None
    blocked_by: list[str] = field(default_factory=list)
    close: dict[str, Any] | None = None


@dataclass
class PaygradeView:
    workers: dict[str, dict[str, Any]]
    removed: dict[str, dict[str, Any]]
    archived_ids: set[str] = field(default_factory=set)


def read_archive_rows(path: Path = ARCHIVE_PATH) -> list[dict[str, str]]:
    """Brackett's rows, in file order."""
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def request_id_for(row: dict[str, str]) -> str:
    """Return a stable idempotency key for this row's write."""
    return f"brackett-cutover-{row['brackett_ref']}"


def load_paygrade_view(client: PaygradeClient) -> PaygradeView:
    """The worker population and the removal feed."""
    employees = client.list_all("listEmployees", company_id=_COMPANY_ID)
    tombstones = client.list_all("listTombstones", since=_SWEEP_FROM_MS)
    return PaygradeView(
        workers={rec["id"]: rec for rec in employees},
        removed={t["id"]: t for t in tombstones},
    )


def _open_placements(row: dict[str, str]) -> int:
    raw = (row.get("open_placements") or "").strip()
    try:
        return int(raw)
    except ValueError:
        return 0


def decide(row: dict[str, str], view: PaygradeView) -> Decision:
    """The :class:`Decision` for one archive row."""
    pg_id = row["pg_id"]
    kind = row["record_kind"]
    view.archived_ids.add(pg_id)

    if kind == "PERIOD":
        # Periods are immutable on Paygrade and the archive already closed
        # them in Brackett's ledger, so the row settles from the file.
        return Decision(outcome=ALREADY_CLOSED)

    if kind == "PLACEMENT":
        # A placement closes with its worker, so the worker's own state is the
        # answer for this row.
        worker_id = (row.get("pg_worker_id") or "").strip()
        tomb = view.removed.get(worker_id)
        if tomb is not None:
            return Decision(outcome=ALREADY_CLOSED, removed_at=tomb["deleted_at"])
        if worker_id in view.workers:
            return Decision(outcome=NO_CLOSE_SURFACE)
        return Decision(outcome=NOT_IN_PAYGRADE)

    tomb = view.removed.get(pg_id)
    if tomb is not None:
        return Decision(outcome=ALREADY_CLOSED, removed_at=tomb["deleted_at"])
    if pg_id not in view.workers:
        return Decision(outcome=NOT_IN_PAYGRADE)

    if _open_placements(row) > 0:
        return Decision(outcome=BLOCKED)

    return Decision(outcome=CLOSED_HERE, close={"id": pg_id, "status": TERMINATED})


def applied_status(response: dict[str, Any]) -> str | None:
    """The employment status Paygrade now holds after this write, or None.

    A refusal on this API is not a status code -- it is an ``error`` object in
    the response document -- so only a document carrying a ``result`` is a
    write that landed.
    """
    if not isinstance(response, dict):
        return None
    if response.get("error") is not None:
        return None
    result = response.get("result")
    if not isinstance(result, dict):
        return None
    return result.get("status")


def discovered_removals(view: PaygradeView) -> list[dict[str, Any]]:
    """Removals Paygrade published that the archive does not carry."""
    return [
        {"entity": t["entity"], "id": t["id"], "deleted_at": t["deleted_at"]}
        for t in sorted(view.removed.values(), key=lambda t: (t["deleted_at"], t["id"]))
        if t["id"] not in view.archived_ids
    ]


def run_migration(cfg: Config) -> dict[str, Any]:
    """Run one closure migration pass end to end."""
    client = PaygradeClient(cfg)
    store = ClosureStore(cfg.output_dir)

    view = load_paygrade_view(client)

    out_rows: list[dict[str, Any]] = []
    closed: list[dict[str, Any]] = []

    for row in read_archive_rows():
        decision = decide(row, view)

        if decision.close is not None:
            payload = dict(decision.close)
            payload.setdefault("request_id", request_id_for(row))
            status = applied_status(client.update_employee(payload))
            if status is not None:
                closed.append({
                    "brackett_ref": row["brackett_ref"],
                    "pg_id": row["pg_id"],
                    "status": status,
                })

        out_rows.append({
            "ref": row["brackett_ref"],
            "record_kind": row["record_kind"],
            "pg_id": row["pg_id"],
            "outcome": decision.outcome,
            "removed_at": decision.removed_at,
            "blocked_by": list(decision.blocked_by),
        })

    counts: dict[str, int] = {}
    for entry in out_rows:
        counts[entry["outcome"]] = counts.get(entry["outcome"], 0) + 1

    discovered = discovered_removals(view)

    store.write_result(out_rows, counts, discovered)
    store.write_import_report(out_rows)
    store.write_writeback_log(closed)
    return {
        "archive_rows": len(out_rows),
        "closed": len(closed),
        "discovered": len(discovered),
        "counts": counts,
    }

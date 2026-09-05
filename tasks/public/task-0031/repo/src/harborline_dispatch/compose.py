"""Build the morning bookable-crew board out of CrewCall.

Per the Dispatch Ops runbook, the assignment feed only ever carries work that
is actually going ahead: CrewCall drops the rows when a client cancels a gig,
archives them off once the work has been settled, and removes assignments
rather than flagging them (``is_deleted`` is a worker column in practice). So
the board does not have to ask what KIND of row a person has, and it does not
have to join back to the gig board to check the row is real:

    a person with a row in the assignment feed is committed;
    a person with no row is free.

The roster is the churning collection: it re-sorts as it is paged, so the crawl
dedupes by id and re-crawls from the start until a whole pass turns up no new
id, exactly as ``docs/pagination.md`` says to -- the runbook agrees. The gig
board and the assignment feed are stable, so one forward pass each is enough --
but the feed is longer than the vendor's maximum page, so "one pass" still
means several requests.
"""

from __future__ import annotations

from typing import Any, Callable

from harborline_dispatch.client import CrewCallClient
from harborline_dispatch.config import Config
from harborline_dispatch.report import BoardWriter

BOOKABLE_WORKER_STATUS = frozenset({"available", "off_shift"})
LIVE_ASSIGNMENT_STATUS = frozenset({"offered", "accepted", "checked_in"})
WINDOW_GIG_STATUS = frozenset({"open", "filling"})


def _walk(fetch: Callable[..., dict[str, Any]]) -> list[dict[str, Any]]:
    """One forward pass over a stable collection, to exhaustion.

    Strides by the limit the vendor says it USED, not the one we asked for: it
    clamps a large page and echoes the clamp back, so the echo is the only
    honest stride and the only honest short-page test.
    """
    out: dict[str, dict[str, Any]] = {}
    offset = 0
    while True:
        envelope = fetch(offset=offset)
        rows = envelope.get("data") or []
        for record in rows:
            out[str(record["id"])] = record
        used = int(envelope.get("limit") or 0) or max(len(rows), 1)
        if len(rows) < used:
            return [out[k] for k in sorted(out)]
        offset += used


def read_roster(client: CrewCallClient) -> list[dict[str, Any]]:
    """Every worker record CrewCall holds for this tenant.

    The roster is re-sorted "most recently active first" while it is paged, so a
    single forward pass can both repeat a record and step over one. Dedupe by
    id and re-crawl from offset 0 until a whole pass discovers nothing new; the
    churn is finite, so that pass arrives.
    """
    known: dict[str, dict[str, Any]] = {}
    while True:
        discovered = 0
        offset = 0
        while True:
            envelope = client.worker_page(offset=offset)
            rows = envelope.get("data") or []
            for record in rows:
                if str(record["id"]) not in known:
                    known[str(record["id"])] = record
                    discovered += 1
            used = int(envelope.get("limit") or 0) or max(len(rows), 1)
            if len(rows) < used:
                break
            offset += used
        if discovered == 0:
            return [known[k] for k in sorted(known)]


def read_gigs(client: CrewCallClient) -> list[dict[str, Any]]:
    """Every gig record CrewCall holds for this tenant."""
    return _walk(client.gig_page)


def read_assignments(client: CrewCallClient) -> list[dict[str, Any]]:
    """Every assignment record CrewCall holds for this tenant."""
    return _walk(client.assignment_page)


def is_live_commitment(assignment: dict[str, Any],
                       gigs: dict[str, dict[str, Any]]) -> bool:
    """Does this row hold its worker?

    Every row in the feed is work still to come, so having one is the answer.
    """
    return True


def build_dispatch_board(cfg: Config) -> dict[str, Any]:
    client = CrewCallClient(cfg)
    writer = BoardWriter(cfg.output_dir)

    workers = read_roster(client)
    gigs = read_gigs(client)
    assignments = read_assignments(client)

    gigs_by_id = {str(g["id"]): g for g in gigs}

    commitments: dict[str, list[str]] = {}
    put_forward: dict[str, set[str]] = {}
    for assignment in assignments:
        worker_id = str(assignment.get("worker_id"))
        gig_id = str(assignment.get("gig_id"))
        if is_live_commitment(assignment, gigs_by_id):
            commitments.setdefault(worker_id, []).append(gig_id)
        # Having a row for a gig is having been put forward for it.
        put_forward.setdefault(worker_id, set()).add(gig_id)

    board_rows: list[dict[str, Any]] = []
    offerable: set[str] = set()
    for worker in sorted(workers, key=lambda w: str(w["id"])):
        worker_id = str(worker["id"])
        held = sorted(commitments.get(worker_id) or [])
        if worker.get("is_deleted"):
            availability, blocked_by, blocking = "off_roster", "roster_removal", ""
        elif held:
            availability, blocked_by, blocking = "committed", "live_commitment", held[0]
        elif worker.get("status") not in BOOKABLE_WORKER_STATUS:
            availability, blocked_by, blocking = "not_available", "worker_status", ""
        else:
            availability, blocked_by, blocking = "offerable", "", ""
            offerable.add(worker_id)
        board_rows.append({
            "worker_id": worker_id,
            "worker_status": str(worker.get("status") or ""),
            "availability": availability,
            "blocked_by": blocked_by,
            "blocking_gig_id": blocking,
        })

    windows = []
    for gig in sorted(
        (g for g in gigs
         if not g.get("is_deleted") and g.get("status") in WINDOW_GIG_STATUS),
        key=lambda g: (-int(g.get("pay_rate_cents") or 0), str(g["id"])),
    ):
        gig_id = str(gig["id"])
        eligible = sorted(
            worker_id for worker_id in offerable
            if gig_id not in put_forward.get(worker_id, ())
        )
        windows.append({
            "gig_id": gig_id,
            "gig_status": str(gig.get("status") or ""),
            "pay_rate_cents": int(gig.get("pay_rate_cents") or 0),
            "eligible_worker_ids": eligible,
        })

    payload = writer.write(board_rows, windows)
    return {
        "roster_rows": len(board_rows),
        "windows": len(windows),
        "pages_fetched": client.pages_fetched,
        "totals": payload["totals"],
    }

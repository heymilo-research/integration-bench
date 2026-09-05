"""The reconciler.

One cycle:

  1. Read the applications RecruitOS has touched since our last cycle and fold
     them into the ATS stage map we carry.
  2. Read the placements Placemint has touched since our last cycle and fold
     them into the placement mirror we carry.
  3. Decide one outcome per crosswalk line, and push where the ATS is the side
     that moved.

Per the Revenue Ops runbook: Placemint's ``updated_at`` is the account
manager's clock and does not move for an API write, so everything the
``modified_since`` feed hands us is somebody else's edit and there is nothing
of our own to filter out. The runbook also records that a retired placement
drops off the feed and out of ``total``, so anything still on the feed is live.
"""

from __future__ import annotations

from typing import Any

from northgate_placement_sync.clients import ApiError, PlacemintClient, RecruitOSClient
from northgate_placement_sync.crosswalk import Link
from northgate_placement_sync.report import CycleResult, CycleRow, WriteRecord

# The tenant's mapping, from the ticket.
STAGE_MAP = {
    "applied": "sourced",
    "interview": "interviewing",
    "offer": "offered",
    "hired": "placed",
    "rejected": "fell_through",
}


def _idempotency_key(cycle: int, placement_id: str, stage: str) -> str:
    return f"ngt-c{cycle}-{placement_id}-{stage}"


def run_cycle(
    *,
    recruitos: RecruitOSClient,
    placemint: PlacemintClient,
    links: list[Link],
    state: dict[str, Any],
) -> tuple[CycleResult, dict[str, Any]]:
    cycle = int(state.get("cycle") or 0) + 1
    first_cycle = cycle == 1

    ats_watermark: str = str(state.get("ats_watermark") or "")
    mkt_watermark: str = str(state.get("marketplace_watermark") or "")
    ats_stage: dict[str, str] = dict(state.get("ats_stage") or {})
    mirror: dict[str, dict[str, Any]] = {
        str(k): dict(v) for k, v in (state.get("mirror") or {}).items()
    }

    # -- 1. what the ATS has done since we last looked -----------------------
    applications = recruitos.applications(modified_since=ats_watermark or None)
    ats_high = ats_watermark
    for row in applications:
        rid = str(row.get("id") or "")
        if not rid:
            continue
        if row.get("is_deleted"):
            ats_stage.pop(rid, None)
        else:
            ats_stage[rid] = str(row.get("stage") or "")
        stamp = str(row.get("updated_at") or "")
        if stamp > ats_high:
            ats_high = stamp

    # -- 2. what the marketplace has done since we last looked ---------------
    feed = placemint.placements(modified_since=mkt_watermark or None)
    moved_by_placemint: set[str] = set()
    mkt_high = mkt_watermark
    for row in feed:
        pid = str(row.get("id") or "")
        if not pid:
            continue
        stamp = str(row.get("updated_at") or "")
        if stamp > mkt_high:
            mkt_high = stamp
        known = mirror.get(pid)
        # The feed is inclusive of the boundary instant, so the row sitting
        # exactly on our stored position comes back unchanged every cycle.
        if not first_cycle and known is not None and known.get("updated_at") != stamp:
            moved_by_placemint.add(pid)
        mirror[pid] = {"stage": str(row.get("stage") or ""), "updated_at": stamp}

    # -- 3. one outcome per crosswalk line -----------------------------------
    rows: list[CycleRow] = []
    writes: list[WriteRecord] = []

    for link in links:
        aid = link.application_id
        pid = link.placement_id
        stage = ats_stage.get(aid, "")
        target = STAGE_MAP.get(stage, "")
        held = mirror.get(pid)

        if held is None:
            rows.append(CycleRow(aid, pid, "unlinked", stage, target, ""))
            continue

        if pid in moved_by_placemint:
            rows.append(CycleRow(aid, pid, "inbound", stage, target, str(held.get("stage") or "")))
            continue

        if target and str(held.get("stage") or "") != target:
            key = _idempotency_key(cycle, pid, target)
            try:
                status, _body = placemint.patch_placement(
                    pid, {"stage": target}, idempotency_key=key
                )
            except ApiError as exc:
                writes.append(WriteRecord(pid, target, int(exc.status), key))
                if exc.status == 404:
                    mirror.pop(pid, None)
                    rows.append(CycleRow(aid, pid, "unlinked", stage, target, ""))
                else:
                    rows.append(
                        CycleRow(aid, pid, "in_sync", stage, target, str(held.get("stage") or ""))
                    )
                continue

            writes.append(WriteRecord(pid, target, int(status), key))
            # The stage is now ours; the timestamp stays the account manager's.
            held["stage"] = target
            rows.append(CycleRow(aid, pid, "pushed", stage, target, target))
            continue

        rows.append(CycleRow(aid, pid, "in_sync", stage, target, str(held.get("stage") or "")))

    result = CycleResult(
        cycle=cycle,
        rows=rows,
        writes=writes,
        ats_watermark=ats_high,
        marketplace_watermark=mkt_high,
    )
    next_state = {
        "cycle": cycle,
        "ats_watermark": ats_high,
        "marketplace_watermark": mkt_high,
        "ats_stage": ats_stage,
        "mirror": mirror,
    }
    return result, next_state

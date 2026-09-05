"""Backfill StaffLine applications and Placemint placements, join by name,
and build the merged roster. See PROBLEM.md."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from staffline_placemint_merge.config import Config
from staffline_placemint_merge.joinkey import join_key
from staffline_placemint_merge.placemint_client import PlacemintClient
from staffline_placemint_merge.staffline_client import StafflineClient


def _placemint_epoch(updated_at: str) -> float:
    return datetime.fromisoformat(updated_at.replace("Z", "+00:00")).timestamp()


def run_merge(config: Config) -> list[dict[str, Any]]:
    sl = StafflineClient(config.staffline_base_url, config.sl_app_token, config.sl_hmac_secret)
    pm = PlacemintClient(config.placemint_base_url, config.pm_client_id, config.pm_client_secret)

    candidates = {c["id"]: c for c in sl.list_all("/svc/candidates")}
    jobs = {j["id"]: j for j in sl.list_all("/svc/jobs")}
    applications = sl.list_all("/svc/applications", params={"include_stage": 1})

    placements = pm.list_all("/api/placements")
    by_join_key: dict[str, list[dict[str, Any]]] = {}
    for p in placements:
        if p.get("is_deleted"):
            continue
        by_join_key.setdefault(join_key(p["candidate_name"]), []).append(p)

    rows: list[dict[str, Any]] = []
    for app in applications:
        candidate = candidates.get(app["candidate_id"])
        job = jobs.get(app["job_id"])
        full_name = f"{candidate.get('fname', '')} {candidate.get('lname', '')}".strip() if candidate else ""
        jkey = join_key(full_name) if full_name else ""

        stage = app["stage"]
        source_of_truth = "staffline"
        placemint_placement_id = None

        matches = by_join_key.get(jkey) if jkey else None
        if matches:
            placement = matches[0]
            sl_ts = float(app.get("mod_ts", 0))
            pm_ts = _placemint_epoch(placement["updated_at"])
            if pm_ts > sl_ts:
                stage = placement["stage"]
                source_of_truth = "placemint"
                placemint_placement_id = placement["id"]

        rows.append(
            {
                "source_id": app["id"],
                "candidate_id": app["candidate_id"],
                "candidate_name": full_name,
                "join_key": jkey,
                "job_id": app["job_id"],
                "job_title": job.get("title") if job else None,
                "staffline_stage": app["stage"],
                "stage": stage,
                "source_of_truth": source_of_truth,
                "placemint_placement_id": placemint_placement_id,
            }
        )

    return rows

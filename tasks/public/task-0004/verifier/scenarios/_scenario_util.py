"""Shared helpers for the task-0004 (StaffLine x Placemint precedence
reconciliation) scenarios.

The connector is driven through the two one-shot subcommands (`merge`,
`correct`) via ``ctx.app.run([...])``. Every scenario recreates BOTH vendors
at the checkpoints that carry this task's world (StaffLine CHECKPOINT=1,
Placemint CHECKPOINT=3) before running anything -- the harness always
force-boots every declared vendor at CHECKPOINT=0, and each scenario is
self-contained rather than assuming a prior scenario's recreate persisted.

Ordering is never proven by comparing StaffLine's and Placemint's request-log
timestamps against each other: `ts` is `time.monotonic()`, which is
per-process and not comparable across two different vendor containers. Every
check below proves ordering/behavior structurally (which calls exist, what
they targeted, what the connector's own output says) instead.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from bench.verifier.io import read_json_output

STAFFLINE = "staffline"
PLACEMINT = "placemint"

SL_CHECKPOINT = 1
PM_CHECKPOINT = 3

# The 15 unambiguous disputed candidates: exactly one StaffLine candidate and
# exactly one ACTIVE Placemint placement share this normalized full name, the
# candidate has exactly one StaffLine application, and that application's
# stage genuinely disagrees with Placemint's -- materialized from actual
# generated state at SL_CHECKPOINT=1 x PM_CHECKPOINT=3 x DATASET_SIZE=400
# (see verifier/fixtures/generate_fixtures.py). Placemint's stage must win.
DISPUTED = [
    {"application_id": "app_0164", "candidate_id": "cand_0014", "placemint_placement_id": "plc_00047", "staffline_stage": "rejected", "target_stage": "offered"},
    {"application_id": "app_0051", "candidate_id": "cand_0027", "placemint_placement_id": "plc_00363", "staffline_stage": "hired", "target_stage": "sourced"},
    {"application_id": "app_0110", "candidate_id": "cand_0043", "placemint_placement_id": "plc_00383", "staffline_stage": "rejected", "target_stage": "offered"},
    {"application_id": "app_0092", "candidate_id": "cand_0060", "placemint_placement_id": "plc_00211", "staffline_stage": "interview", "target_stage": "submitted"},
    {"application_id": "app_0027", "candidate_id": "cand_0064", "placemint_placement_id": "plc_00002", "staffline_stage": "offer", "target_stage": "placed"},
    {"application_id": "app_0066", "candidate_id": "cand_0072", "placemint_placement_id": "plc_00049", "staffline_stage": "hired", "target_stage": "placed"},
    {"application_id": "app_0125", "candidate_id": "cand_0091", "placemint_placement_id": "plc_00199", "staffline_stage": "offer", "target_stage": "fell_through"},
    {"application_id": "app_0172", "candidate_id": "cand_0092", "placemint_placement_id": "plc_00138", "staffline_stage": "offer", "target_stage": "offered"},
    {"application_id": "app_0015", "candidate_id": "cand_0106", "placemint_placement_id": "plc_00152", "staffline_stage": "offer", "target_stage": "placed"},
    {"application_id": "app_0128", "candidate_id": "cand_0120", "placemint_placement_id": "plc_00087", "staffline_stage": "applied", "target_stage": "sourced"},
    {"application_id": "app_0153", "candidate_id": "cand_0121", "placemint_placement_id": "plc_00289", "staffline_stage": "rejected", "target_stage": "sourced"},
    {"application_id": "app_0024", "candidate_id": "cand_0125", "placemint_placement_id": "plc_00097", "staffline_stage": "offer", "target_stage": "interviewing"},
    {"application_id": "app_0093", "candidate_id": "cand_0129", "placemint_placement_id": "plc_00165", "staffline_stage": "applied", "target_stage": "interviewing"},
    {"application_id": "app_0142", "candidate_id": "cand_0132", "placemint_placement_id": "plc_00109", "staffline_stage": "applied", "target_stage": "placed"},
    {"application_id": "app_0159", "candidate_id": "cand_0145", "placemint_placement_id": "plc_00191", "staffline_stage": "rejected", "target_stage": "placed"},
]
DISPUTED_APPLICATION_IDS = sorted(d["application_id"] for d in DISPUTED)
DISPUTED_CANDIDATE_IDS = sorted(d["candidate_id"] for d in DISPUTED)

# The seeded invalid-ref case: app_0155's candidate_id (cand_0017, "Blaise
# Xu") is tombstoned at SL_CHECKPOINT>=1, yet Placemint independently carries
# an ACTIVE placement for the same full name (plc_00046, "Blaise Xu",
# stage=interviewing) at PM_CHECKPOINT=3. A bare name match is never grounds
# to invent a StaffLine record that no longer exists.
INVALID_REF_APPLICATION_ID = "app_0155"
INVALID_REF_CANDIDATE_ID = "cand_0017"
INVALID_REF_PLACEMINT_MATCH_ID = "plc_00046"


def recreate_world(ctx) -> None:
    ctx.vendor(STAFFLINE).recreate(checkpoint=SL_CHECKPOINT)
    ctx.vendor(PLACEMINT).recreate(checkpoint=PM_CHECKPOINT)


def load_fixture(ctx, name: str) -> Any:
    return json.loads((ctx.fixtures / name).read_text(encoding="utf-8"))


def clear_outputs(ctx) -> None:
    for name in ("roster.json", "corrections.json"):
        try:
            (ctx.output_dir / name).unlink()
        except OSError:
            pass


def read_roster(ctx, *, exit_ok: bool = True) -> list[dict[str, Any]] | None:
    return read_json_output(ctx.output_dir / "roster.json", timeout_s=20.0 if exit_ok else 0.5)


def read_corrections(ctx, *, exit_ok: bool = True) -> list[dict[str, Any]] | None:
    return read_json_output(ctx.output_dir / "corrections.json", timeout_s=20.0 if exit_ok else 0.5)


def candidate_get_by_id_calls(request_log: list[dict[str, Any]], candidate_id: str | None = None) -> list[dict[str, Any]]:
    """Every GET /svc/candidates/{id} attempt (excludes the literal
    /svc/candidates/bulk_export path and the plain collection list)."""
    out = []
    for e in request_log or []:
        if str(e.get("method", "")).upper() != "GET":
            continue
        path = str(e.get("path", ""))
        if not path.startswith("/svc/candidates/") or path == "/svc/candidates/bulk_export":
            continue
        if candidate_id is not None and path != f"/svc/candidates/{candidate_id}":
            continue
        out.append(e)
    return out


def create_note_calls(request_log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Every POST /svc/do?action=createNote attempt."""
    out = []
    for e in request_log or []:
        if str(e.get("method", "")).upper() != "POST":
            continue
        if str(e.get("path", "")) != "/svc/do":
            continue
        query = e.get("query") or {}
        if query.get("action") != "createNote":
            continue
        out.append(e)
    return out

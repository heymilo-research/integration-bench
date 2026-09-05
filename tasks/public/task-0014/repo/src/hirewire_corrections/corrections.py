"""Correction-backlog sync (provided). See ``PROBLEM.md``."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Callable

from hirewire_corrections.client import HireWireClient
from hirewire_corrections.config import Config

CORRECTION_STAGE_FROM = "screening"
CORRECTION_STAGE_TO = "rejected"
CORRECTION_EVENT_TYPE = "stage_correction"
CORRECTION_EVENT_NOTE = "auto-rejected by correction sync: stale in screening"


def discover_backlog(client: HireWireClient) -> list[dict[str, Any]]:
    """Non-deleted candidates in ``screening`` (the correction backlog)."""
    page, per_page = 1, 100
    targets: list[dict[str, Any]] = []
    while True:
        status, env = client.list_candidates(page=page, per_page=per_page)
        if status != 200:
            raise RuntimeError(f"list_candidates failed: {status} {env}")
        for rec in env.get("data", []):
            if rec.get("stage") == CORRECTION_STAGE_FROM and not rec.get("is_deleted"):
                targets.append(rec)
        total = env.get("total", 0)
        data = env.get("data", [])
        if not data or page * per_page >= total:
            break
        page += 1
    targets.sort(key=lambda r: r["id"])
    return targets


def _looks_landed(client: HireWireClient, candidate_id: str, before_ts: int) -> bool:
    """Whether a write likely landed after a non-2xx response."""
    _status, env = client.list_candidates(page=1, per_page=120)
    row = next((r for r in env.get("data", []) if r.get("id") == candidate_id), None)
    if row is None:
        return False
    return int(row.get("updated_ts", 0)) > before_ts


def _confirm_or_retry(
    client: HireWireClient,
    candidate_id: str,
    before_ts: int,
    attempt: Callable[[str], tuple[int, dict[str, Any]]],
) -> tuple[bool, dict[str, Any]]:
    """One write attempt; returns ``(ok, body)``."""
    status, body = attempt(str(uuid.uuid4()))
    if 200 <= status < 300:
        return True, body
    if _looks_landed(client, candidate_id, before_ts):
        return True, body
    status2, body2 = attempt(str(uuid.uuid4()))
    return 200 <= status2 < 300, body2


def _post_event(client: HireWireClient, candidate_id: str, before_ts: int) -> tuple[bool, dict[str, Any]]:
    return _confirm_or_retry(
        client,
        candidate_id,
        before_ts,
        lambda key: client.create_event(
            candidate_id, CORRECTION_EVENT_TYPE, CORRECTION_EVENT_NOTE, idempotency_key=key
        ),
    )


def _patch_stage(client: HireWireClient, candidate_id: str, before_ts: int) -> tuple[bool, dict[str, Any]]:
    return _confirm_or_retry(
        client,
        candidate_id,
        before_ts,
        lambda key: client.patch_candidate(candidate_id, {"stage": CORRECTION_STAGE_TO}, idempotency_key=key),
    )


def run_corrections(cfg: Config) -> list[dict[str, Any]]:
    client = HireWireClient(cfg)
    backlog = discover_backlog(client)

    results: list[dict[str, Any]] = []
    for rec in backlog:
        candidate_id = rec["id"]
        before_ts = int(rec.get("updated_ts", 0))

        event_ok, _event_body = _post_event(client, candidate_id, before_ts)
        stage_ok, stage_body = _patch_stage(client, candidate_id, before_ts)

        ok = event_ok and stage_ok
        results.append(
            {
                "candidate_id": candidate_id,
                "ok": ok,
                "stage": stage_body.get("stage") if stage_ok else rec.get("stage"),
            }
        )

    results.sort(key=lambda r: r["candidate_id"])
    write_result(cfg.output_dir, results)
    return results


def write_result(output_dir: Path, results: list[dict[str, Any]]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {"corrections": results}
    out = Path(output_dir) / "writeback_result.json"
    with out.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)

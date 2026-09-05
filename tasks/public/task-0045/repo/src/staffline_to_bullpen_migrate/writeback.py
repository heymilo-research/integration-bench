"""Post-cutover writeback. See ``PROBLEM.md``."""

from __future__ import annotations

from typing import Any

from staffline_to_bullpen_migrate.config import Config
from staffline_to_bullpen_migrate.staffline_client import StafflineClient
from staffline_to_bullpen_migrate.writeback_requests import PENDING_WRITES

RPC_PATH = "/svc/do"

_OP_TO_ACTION = {
    "update_candidate": "updateCandidate",
    "create_note": "createNote",
}


def process_writes(config: Config) -> list[dict[str, Any]]:
    client = StafflineClient(config)
    results: list[dict[str, Any]] = []

    for write in PENDING_WRITES:
        op = write["op"]
        candidate_id = write["candidate_id"]
        payload = {"candidate_id": candidate_id, **write["payload"]}
        action = _OP_TO_ACTION.get(op, op)

        status, body = client.post(RPC_PATH, {"action": action}, payload)
        ok = bool(isinstance(body, dict) and body.get("ok") is True)
        results.append({
            "op": op,
            "candidate_id": candidate_id,
            "ok": ok,
            "id": (body.get("id") if isinstance(body, dict) else None) if ok else None,
            "err": None if ok else (
                body.get("err") if isinstance(body, dict) else f"non-json body (status {status})"
            ),
        })

    return results

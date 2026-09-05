"""Pending writeback batch. See ``PROBLEM.md``."""

from __future__ import annotations

PENDING_WRITES: list[dict] = [
    {
        "op": "update_candidate",
        "candidate_id": "cand_0001",
        "payload": {"status": "placed"},
    },
    {
        "op": "create_note",
        "candidate_id": "cand_0001",
        "payload": {"body": "Migrated from StaffLine; verified by ops.", "author": "migration-bot"},
    },
]

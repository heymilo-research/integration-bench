"""Fixed batch of pending writes for the writeback exercise. See ``PROBLEM.md``."""

from __future__ import annotations

from typing import Any

PENDING_WRITES: list[dict[str, Any]] = [
    {
        "op": "createNote",
        "payload": {
            "candidate_id": "cand_0001",
            "note_text": "Synced from downstream: candidate re-engaged.",
            "created_by": "sync-bot",
        },
    },
    {
        "op": "createNote",
        "payload": {
            "candidate_id": "cand_0002",
            "created_by": "sync-bot",
        },
    },
    {
        "op": "updateCandidate",
        "payload": {
            "candidate_id": "cand_0042",
            "phone": "+1-555-7788",
        },
    },
]

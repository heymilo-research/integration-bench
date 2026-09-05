"""Regenerate task-0040's checkpoint-0 interview fixture.

Deterministic base-seed state (no mutations relevant to this task; the
connector's own reschedule pushes target itv_0100 / itv_0105, DISTINCT from
the seeded timeline's itv_0042). Derived from
``interviewly.state.build_state(seed=3000, checkpoint=0)`` (pure Python, no
server needed — this script imports it directly from the vendor bundle's
``src/`` via ``sys.path``).

Usage (no Docker required):

    python3 verifier/fixtures/generate_fixtures.py

This (re)writes: interviews_checkpoint_0.json

``writeback_result.json`` (the expected POST-CONFIRMATION push outcome) is
hand-authored (see that file) rather than generated here, since it depends on
the live event-confirmation round trip (the 202 -> webhook event -> COMMITTED
transition), not on pure seed-derived state.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_DEFAULT_VENDOR_SRC = Path(__file__).resolve().parents[5] / "vendors" / "interviewly" / "src"
VENDOR_SRC = Path(os.environ.get("INTERVIEWLY_VENDOR_SRC", str(_DEFAULT_VENDOR_SRC)))
sys.path.insert(0, str(VENDOR_SRC))

from interviewly import state  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent
SEED = 3000


def canonical_row(rec: dict) -> dict:
    source_id = rec["source_id"]
    data = {k: v for k, v in rec.items() if k not in ("id", "source_id", "updated_at", "is_deleted")}
    return {
        "source_id": source_id,
        "data": data,
        "updated_at": rec["updated_at"],
        "is_deleted": bool(rec.get("is_deleted", False)),
    }


def dump_checkpoint(checkpoint: int, suffix: str) -> None:
    s = state.build_state(seed=SEED, checkpoint=checkpoint)
    interviews = sorted((canonical_row(r) for r in s["interviews"].values()), key=lambda r: r["source_id"])
    (FIXTURES_DIR / f"interviews_{suffix}.json").write_text(json.dumps(interviews, indent=2), encoding="utf-8")
    print(f"checkpoint={checkpoint}: {len(interviews)} interviews -> interviews_{suffix}.json")


if __name__ == "__main__":
    dump_checkpoint(0, "checkpoint_0")

"""Regenerate task-0024's fixtures (all 4 entities, checkpoints 0 and 2).

Fully deterministic (no Docker needed) via
``talentloop.state.build_state(seed=3015, checkpoint=N)`` -- see task-0023's
fixture generator for the shared rationale on why deleted rows preserve their
last-known ``data``/``updated_at`` unchanged (both discovery paths -- webhook
and poll-reconcile -- converge on that same representation; see
``sync.mark_deleted``'s docstring in the gold connector).

Usage:

    python3 verifier/fixtures/generate_fixtures.py

Regenerates candidates/jobs/applications/notes fixtures at checkpoint 0 and
checkpoint 5 (the spec's "CP2" milestone -- the mutation timeline has 5
entries total; CHECKPOINT=5 applies all of them: cand_0007 delete, job_0003
status update, note_0004 body update, cand_0055 pipeline_status update
(coincidentally the same value as its base-seed status -- only modified_at
actually changes; a connector must not skip applying a "no-op-looking" update),
and app_0009 delete).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_DEFAULT_VENDOR_SRC = Path(__file__).resolve().parents[5] / "vendors" / "talentloop" / "src"
VENDOR_SRC = Path(os.environ.get("TALENTLOOP_VENDOR_SRC", str(_DEFAULT_VENDOR_SRC)))
sys.path.insert(0, str(VENDOR_SRC))

from talentloop import state  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent
SEED = 3015

_KIND_KEYS = {
    "candidates": "candidates",
    "jobs": "jobs",
    "applications": "applications",
    "notes": "notes",
}


def canonical(rec: dict, *, is_deleted: bool = False) -> dict:
    source_id = rec.get("source_id") or rec["id"]
    updated_at = str(rec["modified_at"])
    data = {k: v for k, v in rec.items() if k != "source_id"}
    return {"source_id": source_id, "data": data, "updated_at": updated_at, "is_deleted": is_deleted}


def dump_checkpoint(checkpoint: int, filenames: dict[str, str], *, base_records: dict | None = None) -> None:
    app_state, deleted = state.build_state(seed=SEED, checkpoint=checkpoint)

    for plural, fname in filenames.items():
        rows = [canonical(r) for r in app_state[plural].values()]
        base = (base_records or {}).get(plural, {})
        for eid in deleted[plural]:
            base_rec = base.get(eid)
            if base_rec is not None:
                rows.append(canonical(base_rec, is_deleted=True))
        rows.sort(key=lambda r: r["source_id"])
        (FIXTURES_DIR / fname).write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
        print(f"checkpoint={checkpoint}: {len(rows)} {plural} -> {fname}")


if __name__ == "__main__":
    cp0_state, _cp0_deleted = state.build_state(seed=SEED, checkpoint=0)
    dump_checkpoint(0, {
        "candidates": "candidates_checkpoint_0.json",
        "jobs": "jobs_checkpoint_0.json",
        "applications": "applications_checkpoint_0.json",
        "notes": "notes_checkpoint_0.json",
    })
    dump_checkpoint(5, {
        "candidates": "candidates_post_cp2.json",
        "jobs": "jobs_post_cp2.json",
        "applications": "applications_post_cp2.json",
        "notes": "notes_post_cp2.json",
    }, base_records=cp0_state)

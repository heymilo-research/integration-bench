"""Regenerate / cross-check task-0012's fixtures (all 4 entities at
checkpoints 0 and 5, plus the writeback_result.json cross-check).

Fully deterministic (no Docker needed) for the polled-entity fixtures, via
``talentloop.state.build_state(seed=3015, checkpoint=N)`` -- this task shares
the EXACT same seed/mutations.yaml as task-0023/0024/0025, so the
checkpoint-0 and checkpoint-5 ("CP2" milestone -- 5 mutation-timeline
entries total) fixtures are byte-identical to task-0024's and are simply
copied from there (see this directory's checked-in files).

The writeback_result.json cross-check below re-derives the expected shape
from the vendor's own note-create logic
(``vendors/talentloop/src/talentloop/main.py::create_note``):
  - note ids are assigned ``note_9{seq:04d}`` in CREATION ORDER, counter
    starting at 1 and incrementing ONLY on a successful (2xx) create --
    the malformed (empty-body) item 422s BEFORE the counter increments, so
    it consumes no id.
  - created_at/modified_at on a freshly written note are stamped with the
    FIXED ``mutations.BASE_EPOCH_S`` constant (2026-03-14T10:00:00Z), not a
    wall clock -- reproducible regardless of when this push actually runs.
  - this cross-check assumes ``repo/input/pending_writeback.json``'s item
    order (wb-001-note valid, wb-002-bad-note malformed) and a
    freshly-recreated (checkpoint=0) vendor, since the id counter resets to
    zero on every container boot.

Usage (no Docker required):

    python3 verifier/fixtures/generate_fixtures.py

To validate end-to-end against the real running image:

    docker compose up -d vendor postgres
    docker compose run --rm app python -m talentloop_summit backfill
    docker compose run --rm app python -m talentloop_summit dump
    # diff output/{candidates,jobs,applications,notes}.json against the
    # *_checkpoint_0.json fixtures
    docker compose run --rm app python -m talentloop_summit push
    # diff output/writeback_result.json against writeback_result.json, then
    # re-run push and confirm byte-identical output (idempotent retry)
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_DEFAULT_VENDOR_SRC = Path(__file__).resolve().parents[5] / "vendors" / "talentloop" / "src"
VENDOR_SRC = Path(os.environ.get("TALENTLOOP_VENDOR_SRC", str(_DEFAULT_VENDOR_SRC)))
sys.path.insert(0, str(VENDOR_SRC))

from talentloop import state  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent
SEED = 3015
BASE_EPOCH_S = 1773482400  # 2026-03-14T10:00:00Z, matches mutations.yaml

_FILENAMES = {
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


def dump_checkpoint(checkpoint: int, suffix: str, *, base_records: dict | None = None) -> None:
    app_state, deleted = state.build_state(seed=SEED, checkpoint=checkpoint)
    for plural in _FILENAMES:
        rows = [canonical(r) for r in app_state[plural].values()]
        base = (base_records or {}).get(plural, {})
        for eid in deleted[plural]:
            base_rec = base.get(eid)
            if base_rec is not None:
                rows.append(canonical(base_rec, is_deleted=True))
        rows.sort(key=lambda r: r["source_id"])
        fname = f"{plural}_{suffix}.json"
        (FIXTURES_DIR / fname).write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
        print(f"checkpoint={checkpoint}: {len(rows)} {plural} -> {fname}")


def print_expected_writeback_result() -> None:
    """Re-derive the expected writeback_result.json for a cross-check (does
    not overwrite -- the shipped fixture already reflects this)."""
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(BASE_EPOCH_S))
    expected = {
        "events": [
            {
                "client_ref": "wb-001-note",
                "ok": True,
                "record": {
                    "id": "note_90001", "source_id": "note_90001", "candidate_id": "cand_0010",
                    "body": "Reference check completed; strong fit for backend roles.",
                    "author": "sync-bot@tl.test", "created_at": iso, "modified_at": iso,
                },
            },
            {
                "client_ref": "wb-002-bad-note",
                "ok": False,
                "error": {"status": 422, "field_errors": {"body": ["is required"]}},
            },
        ]
    }
    expected["events"].sort(key=lambda r: r["client_ref"])
    print(json.dumps(expected, indent=2, sort_keys=True))


if __name__ == "__main__":
    cp0_state, _cp0_deleted = state.build_state(seed=SEED, checkpoint=0)
    dump_checkpoint(0, "checkpoint_0")
    # CP1 (index0, cand_0007 delete) -- used by dropped_delete_reconcile.py.
    # Only candidates change; jobs/applications/notes are identical to cp0.
    dump_checkpoint(1, "post_cp1", base_records=cp0_state)
    dump_checkpoint(5, "post_cp2", base_records=cp0_state)
    print("\n--- expected writeback_result.json (cross-check against the shipped fixture) ---")
    print_expected_writeback_result()

"""Regenerate / cross-check task-0011's fixtures.

Everything in this task's fixtures is DETERMINISTIC and derivable without a
live vendor container, because:

  - the base seed data (candidates/applications) comes only from
    ``talentforge.state.build_state(seed=2000, checkpoint=N)`` (pure Python,
    no server needed -- this script imports it directly from the vendor
    bundle's ``src/`` via ``sys.path``), and
  - the vendor's writeback endpoints stamp ``created_at``/``modified_at`` on
    new/updated writeback records with the fixed ``mutations.BASE_EPOCH_MS``
    constant, NOT a wall clock (see
    ``vendors/talentforge/src/talentforge/main.py``'s ``create_candidate`` /
    ``update_candidate`` / ``create_note``) -- so the ``writeback_result.json``
    fixture's timestamps are reproducible too, PROVIDED the push loop issues
    writes against a freshly-recreated (checkpoint=0) vendor in the same
    order the fixture assumes (note create, candidate PATCH, candidate
    create, malformed note create -- matching
    ``repo/input/pending_writeback.json``'s item order), since the vendor's
    per-kind id sequence counters (``cand_9000N`` / ``note_9000N``) reset to
    zero on every container boot and increment only on a SUCCESSFUL create.

Usage (no Docker required for the candidates/applications fixtures):

    python3 verifier/fixtures/generate_fixtures.py

This regenerates:
    candidates_checkpoint_0.json / applications_checkpoint_0.json
    candidates_post_cp4.json     / applications_post_cp4.json

and re-derives (prints, does not overwrite) the expected
``writeback_result.json`` shape as a cross-check.

To VALIDATE the fixtures against the real running image end-to-end (recommended
before `bench validate`), run the actual connector against `talentforge:local`:

    docker compose up -d vendor postgres
    docker compose run --rm app python -m talentforge_hooks backfill
    docker compose run --rm app python -m talentforge_hooks dump
    # diff output/candidates.json / output/applications.json against the cp0 fixtures

    docker compose exec vendor sh -c 'kill -0 1'   # (recreate at checkpoint 4:
    # set CHECKPOINT=4 via an override file / `bench`'s VendorHandle.recreate,
    # bring up `app serve`, drain, then `dump` again and diff against
    # candidates_post_cp4.json / applications_post_cp4.json)

    docker compose run --rm app python -m talentforge_hooks push
    # diff output/writeback_result.json against writeback_result.json, then
    # run push a second time and confirm the output is byte-identical
    # (idempotent retry).
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Resolve the canonical monorepo vendor source; allow an explicit authoring override.
_DEFAULT_VENDOR_SRC = Path(__file__).resolve().parents[5] / "vendors" / "talentforge" / "src"
VENDOR_SRC = Path(os.environ.get("TALENTFORGE_VENDOR_SRC", str(_DEFAULT_VENDOR_SRC)))
sys.path.insert(0, str(VENDOR_SRC))

from talentforge import mutations, state  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent
SEED = 2000


def canonical_candidate(rec: dict) -> dict:
    source_id = rec.get("source_id") or rec["id"]
    updated_at = str(int(rec["modified_at"]))
    is_deleted = bool(rec.get("is_deleted", False))
    data = {k: v for k, v in rec.items() if k != "source_id"}
    return {"source_id": source_id, "data": data, "updated_at": updated_at, "is_deleted": is_deleted}


def canonical_application(rec: dict) -> dict:
    source_id = rec.get("source_id") or rec["id"]
    updated_at = str(rec["modified_at"])
    is_deleted = bool(rec.get("is_deleted", False))
    data = {k: v for k, v in rec.items() if k != "source_id"}
    return {"source_id": source_id, "data": data, "updated_at": updated_at, "is_deleted": is_deleted}


def dump_checkpoint(checkpoint: int, cand_name: str, app_name: str) -> None:
    s = state.build_state(seed=SEED, checkpoint=checkpoint)
    candidates = sorted((canonical_candidate(r) for r in s["candidates"].values()), key=lambda r: r["source_id"])
    applications = sorted((canonical_application(r) for r in s["applications"].values()), key=lambda r: r["source_id"])
    (FIXTURES_DIR / cand_name).write_text(json.dumps(candidates, indent=2, sort_keys=True), encoding="utf-8")
    (FIXTURES_DIR / app_name).write_text(json.dumps(applications, indent=2, sort_keys=True), encoding="utf-8")
    print(f"checkpoint={checkpoint}: {len(candidates)} candidates, {len(applications)} applications -> {cand_name}, {app_name}")


def print_expected_writeback_result() -> None:
    """Re-derive the expected writeback_result.json for a cross-check (does
    not overwrite -- the shipped fixture already reflects this)."""
    s = state.build_state(seed=SEED, checkpoint=0)
    cand_0020 = dict(s["candidates"]["cand_0020"])
    cand_0020["pipeline_status"] = "placed"
    cand_0020["modified_at"] = mutations.BASE_EPOCH_MS
    iso = __import__("time").strftime("%Y-%m-%dT%H:%M:%SZ", __import__("time").gmtime(mutations.BASE_EPOCH_S))
    expected = {
        "events": [
            {
                "client_ref": "wb-001-note", "ok": True, "kind": "note",
                "record": {
                    "id": "note_90001", "source_id": "note_90001", "candidate_id": "cand_0010",
                    "body": "Reference check completed; strong fit for backend roles.",
                    "author": "sync-bot@tf.test", "created_at": iso, "modified_at": iso,
                    "is_deleted": False,
                },
            },
            {"client_ref": "wb-002-stage", "ok": True, "kind": "candidate_update", "record": cand_0020},
            {
                "client_ref": "wb-003-create", "ok": True, "kind": "candidate_create",
                "record": {
                    "id": "cand_90001", "source_id": "cand_90001", "given_name": "Priya",
                    "family_name": "Shah", "email": "priya.shah@example.test", "phone": "",
                    "pipeline_status": "new", "created_at": mutations.BASE_EPOCH_MS,
                    "modified_at": mutations.BASE_EPOCH_MS, "is_deleted": False,
                },
            },
            {
                "client_ref": "wb-004-bad-note", "ok": False, "kind": "note",
                "error": {"status": 422, "field_errors": {"body": ["is required"]}},
            },
        ]
    }
    expected["events"].sort(key=lambda r: r["client_ref"])
    print(json.dumps(expected, indent=2, sort_keys=True))


if __name__ == "__main__":
    dump_checkpoint(0, "candidates_checkpoint_0.json", "applications_checkpoint_0.json")
    dump_checkpoint(4, "candidates_post_cp4.json", "applications_post_cp4.json")
    print("\n--- expected writeback_result.json (cross-check against the shipped fixture) ---")
    print_expected_writeback_result()

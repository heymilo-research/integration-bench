"""Regenerate task-0023's fixtures.

The candidates/applications base state is fully deterministic (pure Python,
no server needed) via ``talentloop.state.build_state(seed=3015,
checkpoint=N)`` -- this script imports it directly from the vendor bundle's
``src/`` via ``sys.path``.

**Both** of this task's delete-discovery paths (a consumed
``candidate.deleted`` webhook event, and a poll-only vanish+410 reconcile
sweep) are engineered by the gold connector (see ``sync.mark_deleted``'s
docstring) to converge on an IDENTICAL row for a deleted record: the record's
last-known ``data``/``updated_at`` preserved unchanged, with only
``is_deleted`` flipped to ``true``. Neither delete-discovery signal carries a
fresh timestamp for the deletion itself, so inventing one (e.g. wall-clock
"now") would make the poll-only scenario's fixture non-deterministic across
runs -- this script (and the gold connector) deliberately do not do that.
This means ONE `candidates_post_cp1.json` fixture is valid for BOTH the
`webhook_delete_freshness` and `poll_reconcile` scenarios.

Usage (no Docker required):

    python3 verifier/fixtures/generate_fixtures.py

Regenerates:
    candidates_checkpoint_0.json / applications_checkpoint_0.json
    candidates_post_cp1.json     / applications_post_cp1.json  (cand_0007 tombstoned;
                                                                  applications unchanged
                                                                  at CP1)

To validate fixtures end-to-end against the real running image:

    docker compose up -d vendor postgres
    docker compose run --rm app python -m talentloop_deletes backfill
    docker compose run --rm app python -m talentloop_deletes dump
    # diff output/*.json against the *_checkpoint_0.json fixtures

    # recreate vendor at CHECKPOINT=1 (e.g. via an override file), then either:
    #   (a) bring up `app serve` + drain webhooks, `dump` again, diff against
    #       *_post_cp1.json  -- proves the webhook delete path, or
    #   (b) run `app poll` (no serve), `dump` again, diff against
    #       *_post_cp1.json  -- proves the poll-only reconcile path.
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


def canonical(rec: dict, *, is_deleted: bool = False) -> dict:
    source_id = rec.get("source_id") or rec["id"]
    updated_at = str(rec["modified_at"])
    data = {k: v for k, v in rec.items() if k != "source_id"}
    return {"source_id": source_id, "data": data, "updated_at": updated_at, "is_deleted": is_deleted}


def dump_checkpoint(checkpoint: int, cand_name: str, app_name: str, *, base_checkpoint_records: dict | None = None) -> None:
    app_state, deleted = state.build_state(seed=SEED, checkpoint=checkpoint)

    candidates = [canonical(r) for r in app_state["candidates"].values()]
    # Tombstones for ids deleted by this checkpoint: preserve last-known data
    # from the BASE (pre-delete) record -- deterministic, matches the gold
    # connector's mark_deleted() semantics (no invented timestamp).
    base_candidates = (base_checkpoint_records or {}).get("candidates", {})
    for cid in deleted["candidates"]:
        base_rec = base_candidates.get(cid)
        if base_rec is not None:
            candidates.append(canonical(base_rec, is_deleted=True))

    applications = [canonical(r) for r in app_state["applications"].values()]
    base_applications = (base_checkpoint_records or {}).get("applications", {})
    for aid in deleted["applications"]:
        base_rec = base_applications.get(aid)
        if base_rec is not None:
            applications.append(canonical(base_rec, is_deleted=True))

    candidates.sort(key=lambda r: r["source_id"])
    applications.sort(key=lambda r: r["source_id"])

    (FIXTURES_DIR / cand_name).write_text(json.dumps(candidates, indent=2, sort_keys=True), encoding="utf-8")
    (FIXTURES_DIR / app_name).write_text(json.dumps(applications, indent=2, sort_keys=True), encoding="utf-8")
    print(f"checkpoint={checkpoint}: {len(candidates)} candidates, {len(applications)} applications -> {cand_name}, {app_name}")


if __name__ == "__main__":
    cp0_state, _cp0_deleted = state.build_state(seed=SEED, checkpoint=0)
    dump_checkpoint(0, "candidates_checkpoint_0.json", "applications_checkpoint_0.json")
    dump_checkpoint(
        1, "candidates_post_cp1.json", "applications_post_cp1.json",
        base_checkpoint_records=cp0_state,
    )

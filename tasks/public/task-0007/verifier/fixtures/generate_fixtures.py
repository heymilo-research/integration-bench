"""Regenerate task-0007's answer-key fixtures.

The canonical rows in these fixtures are FULLY DETERMINISTIC and derivable
without a live vendor container: the base seed data + mutation timeline come
only from ``vettly.state.build_state(seed=3100, checkpoint=N)`` (pure Python,
no server needed — this script imports it directly from the vendor bundle's
``src/`` via ``sys.path``), and the canonical mapping below (finished_at ->
completed_at, epoch-seconds passthrough) mirrors the gold ``sync.py``.

None of this task's faults (VT_CURSOR_TTL_REQS, FAULT_TOKEN_EXPIRY_MIDRUN)
change the vendor's underlying REST state — they change the PATH a connector
takes to reach it, never the data itself — so these fixtures are the answer
key for every scenario in this task, faulted or not.

Usage (no Docker required):

    python3 verifier/fixtures/generate_fixtures.py

This (re)writes:
    subjects_checkpoint_0.json / checks_checkpoint_0.json / reports_checkpoint_0.json
    subjects_checkpoint_5.json / checks_checkpoint_5.json / reports_checkpoint_5.json

To VALIDATE end-to-end against the real running image (recommended before
`bench validate`), run the actual connector against `vettly:local`:

    docker compose up -d vendor postgres
    docker compose run --rm app python -m vettly_sync sync
    docker compose run --rm app python -m vettly_sync dump
    # diff output/{subjects,checks,reports}.json against the _checkpoint_0 fixtures

    # recreate the vendor at CHECKPOINT=5 (all 5 seeded mutations applied), then:
    docker compose run --rm app python -m vettly_sync sync
    docker compose run --rm app python -m vettly_sync dump
    # diff output/{subjects,checks,reports}.json against the _checkpoint_5 fixtures
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Resolve the canonical monorepo vendor source; allow an explicit authoring override.
_DEFAULT_VENDOR_SRC = Path(__file__).resolve().parents[5] / "vendors" / "vettly" / "src"
VENDOR_SRC = Path(os.environ.get("VETTLY_VENDOR_SRC", str(_DEFAULT_VENDOR_SRC)))
sys.path.insert(0, str(VENDOR_SRC))

from vettly import state  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent
SEED = 3100
MUTATED_CHECKPOINT = 5  # all 5 seeded mutations applied (mutations.yaml)


def canonical_row(rec: dict, *, is_report: bool) -> dict:
    source_id = rec["source_id"]
    data = {k: v for k, v in rec.items() if k not in ("id", "source_id", "updated_at", "is_deleted")}
    if is_report:
        data["completed_at"] = data.pop("finished_at", None)
    return {
        "source_id": source_id,
        "data": data,
        "updated_at": rec["updated_at"],
        "is_deleted": bool(rec.get("is_deleted", False)),
    }


def dump_checkpoint(checkpoint: int) -> None:
    s = state.build_state(seed=SEED, checkpoint=checkpoint)
    subjects = sorted((canonical_row(r, is_report=False) for r in s["subjects"].values()), key=lambda r: r["source_id"])
    checks = sorted((canonical_row(r, is_report=False) for r in s["checks"].values()), key=lambda r: r["source_id"])
    reports = sorted((canonical_row(r, is_report=True) for r in s["reports"].values()), key=lambda r: r["source_id"])

    (FIXTURES_DIR / f"subjects_checkpoint_{checkpoint}.json").write_text(json.dumps(subjects, indent=2), encoding="utf-8")
    (FIXTURES_DIR / f"checks_checkpoint_{checkpoint}.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    (FIXTURES_DIR / f"reports_checkpoint_{checkpoint}.json").write_text(json.dumps(reports, indent=2), encoding="utf-8")
    print(
        f"checkpoint={checkpoint}: {len(subjects)} subjects, {len(checks)} checks, "
        f"{len(reports)} reports -> *_checkpoint_{checkpoint}.json"
    )


if __name__ == "__main__":
    dump_checkpoint(0)
    dump_checkpoint(MUTATED_CHECKPOINT)

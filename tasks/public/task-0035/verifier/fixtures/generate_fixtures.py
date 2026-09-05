"""Regenerate task-0035's fixtures.

The canonical rows in these fixtures are FULLY DETERMINISTIC and derivable
without a live vendor container: the base seed data + mutation timeline come
only from ``vettly.state.build_state(seed=3100, checkpoint=N)`` (pure Python,
no server needed — this script imports it directly from the vendor bundle's
``src/`` via ``sys.path``), and the canonical mapping below (finished_at ->
completed_at, epoch-seconds passthrough) mirrors the gold ``sync.py``.

Usage (no Docker required):

    python3 verifier/fixtures/generate_fixtures.py

This (re)writes:
    subjects_checkpoint_0.json / checks_checkpoint_0.json / reports_checkpoint_0.json
    subjects_checkpoint_1.json / checks_checkpoint_1.json / reports_checkpoint_1.json

To VALIDATE end-to-end against the real running image (recommended before
`bench validate`), run the actual connector against `vettly:local`:

    docker compose up -d vendor
    docker compose run --rm app python -m vettly_sync
    # diff output/{subjects,checks,reports}.json against the _checkpoint_0 fixtures

    # recreate the vendor at CHECKPOINT=5 (all 5 seeded mutations applied),
    # then:
    docker compose run --rm app python -m vettly_sync --incremental
    # diff output/{subjects,checks,reports}.json against the _checkpoint_1 fixtures

    # separately, inspect /var/log/vendor/tokens.jsonl and requests.jsonl to
    # confirm single-use rotation held and that the backfill actually crossed
    # a token lifetime (more than one access_token minted).
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


def dump_checkpoint(checkpoint: int, suffix: str) -> None:
    s = state.build_state(seed=SEED, checkpoint=checkpoint)
    subjects = sorted((canonical_row(r, is_report=False) for r in s["subjects"].values()), key=lambda r: r["source_id"])
    checks = sorted((canonical_row(r, is_report=False) for r in s["checks"].values()), key=lambda r: r["source_id"])
    reports = sorted((canonical_row(r, is_report=True) for r in s["reports"].values()), key=lambda r: r["source_id"])

    (FIXTURES_DIR / f"subjects_{suffix}.json").write_text(json.dumps(subjects, indent=2), encoding="utf-8")
    (FIXTURES_DIR / f"checks_{suffix}.json").write_text(json.dumps(checks, indent=2), encoding="utf-8")
    (FIXTURES_DIR / f"reports_{suffix}.json").write_text(json.dumps(reports, indent=2), encoding="utf-8")
    print(f"checkpoint={checkpoint}: {len(subjects)} subjects, {len(checks)} checks, {len(reports)} reports -> *_{suffix}.json")


if __name__ == "__main__":
    dump_checkpoint(0, "checkpoint_0")
    dump_checkpoint(5, "checkpoint_1")  # all 5 seeded mutations applied

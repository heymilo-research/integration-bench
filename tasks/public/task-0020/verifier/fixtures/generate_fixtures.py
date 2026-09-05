"""Regenerate / cross-check task-0020's candidate fixtures.

Everything here is DETERMINISTIC and derivable without a live vendor
container, because the base seed data AND the scripted mutation timeline come
only from ``globalhire.state.build_state(seed=5000, checkpoint=N)`` (pure
Python, no server needed — this script imports it directly from the vendor
bundle's ``src/`` via ``sys.path``). ``build_state`` already renders each
record's ``created_at``/``modified_at`` in that record's own per-record
numeric offset (the "offset chaos" quirk) and stamps the internal
``_utc_s``/``_offset`` bookkeeping keys the wire strips before serving.

2026-08 hardening pass (rework spec §3b): the checkpoint-1 mutation set is
UNCHANGED — the new tombstone/ordering leg composes the EXISTING
``FAULT_5XX_ON_PAGE`` knob onto this SAME timeline from the verifier side
(see ``verifier/scenarios/incremental.py``), it does not touch fixture data.
Regenerating here is a cross-check that the checked-in fixtures still match
the current vendor source (relevant since the vendor gained the GH_V2_*
dual-version machinery in this same pass — off by default, and per
``vendor.yaml``/``main.py`` byte-identical to a build with no v2 knowledge at
all when unset, so this v1-only task's candidate data must be unaffected).

Usage (no Docker required):

    python3 verifier/fixtures/generate_fixtures.py

Regenerates and diffs against the checked-in fixtures (does not overwrite
unless ``--write`` is passed):

    candidates_checkpoint_0.json   (initial-sync world, 6000 rows)
    candidates_checkpoint_1.json   (post-mutation world, 6001 rows: update
                                     cand_00042, tombstone cand_00017,
                                     create cand_09000)

To validate end-to-end against the real running image (recommended before
`bench validate`), run the actual connector against `globalhire:local`:

    docker compose up -d vendor postgres
    docker compose run --rm app python -m globalhire_sync sync
    docker compose run --rm app python -m globalhire_sync dump
    # diff output/candidates.json against candidates_checkpoint_0.json

    # recreate vendor at CHECKPOINT=1 (e.g. via an override file / bench's
    # VendorHandle.recreate), optionally with FAULT_5XX_ON_PAGE=0:2 set, then:
    docker compose run --rm app python -m globalhire_sync sync
    docker compose run --rm app python -m globalhire_sync dump
    # diff output/candidates.json against candidates_checkpoint_1.json
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Resolve the canonical monorepo vendor source; allow an explicit authoring override.
_DEFAULT_VENDOR_SRC = (
    Path(__file__).resolve().parents[5] / "vendors" / "globalhire" / "src"
)
VENDOR_SRC = Path(os.environ.get("GLOBALHIRE_VENDOR_SRC", str(_DEFAULT_VENDOR_SRC)))
sys.path.insert(0, str(VENDOR_SRC))

from globalhire import state  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent
SEED = 5000

# Internal-only keys the wire strips before serialization (mirrors
# globalhire.main._public / _INTERNAL_KEYS).
_INTERNAL_KEYS = ("_utc_s", "_offset")


def _public(rec: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in rec.items() if k not in _INTERNAL_KEYS}


def _iso_offset_to_utc_s(value: str) -> int:
    """Mirrors the gold connector's globalhire_sync.sync.iso_offset_to_utc_s:
    parse a wire timestamp (ISO-8601 with a numeric offset) to a UTC epoch
    second, honoring the offset."""
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.astimezone(timezone.utc).timestamp())


def canonical_candidate(rec: dict[str, Any]) -> dict[str, Any]:
    """Mirrors globalhire_sync.sync.canonical_from_candidate exactly."""
    wire = _public(rec)
    return {
        "source_id": wire["id"],
        "data": dict(wire),
        "updated_at": _iso_offset_to_utc_s(wire["modified_at"]),
        "is_deleted": bool(wire.get("is_deleted", False)),
    }


def dump_checkpoint(checkpoint: int) -> list[dict[str, Any]]:
    s = state.build_state(seed=SEED, checkpoint=checkpoint)
    return sorted(
        (canonical_candidate(r) for r in s["candidates"].values()),
        key=lambda r: r["source_id"],
    )


def _write(name: str, rows: list[dict[str, Any]]) -> None:
    (FIXTURES_DIR / name).write_text(
        json.dumps(rows, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _diff(name: str, rows: list[dict[str, Any]]) -> bool:
    """Compare freshly-derived rows against the checked-in fixture. Returns
    True iff they match (structural JSON equality, the same equivalence the
    verifier scenarios use: `store == load_fixture(...)`)."""
    path = FIXTURES_DIR / name
    if not path.is_file():
        print(f"  {name}: MISSING on disk")
        return False
    existing = json.loads(path.read_text(encoding="utf-8"))
    if existing == rows:
        print(f"  {name}: MATCH ({len(rows)} rows)")
        return True
    existing_ids = {r["source_id"] for r in existing}
    fresh_ids = {r["source_id"] for r in rows}
    print(
        f"  {name}: MISMATCH — existing={len(existing)} fresh={len(rows)} "
        f"only_in_existing={sorted(existing_ids - fresh_ids)[:5]} "
        f"only_in_fresh={sorted(fresh_ids - existing_ids)[:5]}"
    )
    return False


def main() -> int:
    write = "--write" in sys.argv
    ok = True
    for checkpoint, name in ((0, "candidates_checkpoint_0.json"), (1, "candidates_checkpoint_1.json")):
        rows = dump_checkpoint(checkpoint)
        if write:
            _write(name, rows)
            print(f"  {name}: WROTE ({len(rows)} rows)")
        else:
            ok = _diff(name, rows) and ok
    if not write:
        print("OK — fixtures match the vendor source." if ok else "MISMATCH — see above; re-run with --write to regenerate.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

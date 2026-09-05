"""The nightly requisition rollup.

This is the pass Data Services left behind. ``run_rollup`` is the
orchestration -- it counts the rows, assembles ``result.json`` and hands both
artifacts to ``store.py`` -- and does not need changing. Everything that is
specific to where tonight's rows come FROM lives in ``build_rollup`` and the
``SOURCE`` constant above it.

Today that source is RecruitOS's Reporting Mart: the mart derived every
column itself, so this pass copies its lines through and renames two of them.

Data Services' own handover note is in ``docs/sandhurst-mart-handover.md``.
Full RecruitOS documentation is in ``docs/``; start at ``docs/index.md``.
"""

from __future__ import annotations

from typing import Any

from sandhurst_rollup import drop
from sandhurst_rollup.config import Config
from sandhurst_rollup.store import RollupStore

# Where tonight's rows come from. Reported verbatim in result.json so the
# on-call dashboard can tell which pipeline produced the file.
SOURCE = "mart-drop"

DISPOSITIONS = ("dropped", "placed", "lost", "frozen", "working")


def build_rollup(cfg: Config) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Tonight's rollup rows, plus the retired-record tallies.

    One row per application in the tenant, each carrying the six columns
    Finance's loader reads. The mart's drop already holds every one of them,
    so the mart-era pass is a rename: ``req_id`` -> ``requisition_id`` and
    ``state`` -> ``disposition``.
    """
    rows: list[dict[str, Any]] = []
    for line in drop.read_latest_drop(cfg):
        rows.append({
            "application_id": line.get("application_id"),
            "candidate_id": line.get("candidate_id"),
            "requisition_id": line.get("req_id"),
            "stage": line.get("stage"),
            "disposition": line.get("state"),
            "last_change_at": line.get("last_change_at"),
        })
    retired = {"candidates": 0, "requisitions": 0}
    return rows, retired


def run_rollup(cfg: Config) -> dict[str, Any]:
    """One nightly rollup pass."""
    store = RollupStore(cfg.output_dir)
    rows, retired = build_rollup(cfg)

    counts: dict[str, int] = {"rows": len(rows)}
    for name in DISPOSITIONS:
        counts[name] = sum(1 for row in rows if str(row.get("disposition")) == name)

    summary = {
        "source": SOURCE,
        "counts": counts,
        "retired": retired,
        "rows": rows,
    }
    store.write_result(summary)
    store.write_rollup(rows)
    return summary

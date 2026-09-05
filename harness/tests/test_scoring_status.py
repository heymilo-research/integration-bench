from __future__ import annotations

import json
from pathlib import Path

from bench.commands.scoring_status import render, scoring_status


def _task(tmp: Path, name: str, checks_v2: list[dict] | None, write=True) -> None:
    d = tmp / name / "verifier"
    d.mkdir(parents=True)
    if not write:
        return
    payload = {"schema": 2, "n_total": len(checks_v2 or [])}
    if checks_v2 is not None:
        payload["checks_v2"] = checks_v2
    (d / "empty-baseline.json").write_text(json.dumps(payload), encoding="utf-8")


def test_reports_unauthored_tasks(tmp_path: Path):
    _task(tmp_path, "task-0001", [{"bucket": "l1", "name": "a", "gold": True}])
    rows = scoring_status(tmp_path)
    assert len(rows) == 1
    assert rows[0].ok is False
    assert any("no mandatory" in e for e in rows[0].errors)


def test_missing_sidecar_is_a_failure_not_a_skip(tmp_path: Path):
    """A task with no sidecar must not silently count as fine."""
    _task(tmp_path, "task-0002", None, write=False)
    rows = scoring_status(tmp_path)
    assert rows[0].ok is False
    assert "no verifier/empty-baseline.json" in rows[0].errors[0]


def test_schema1_sidecar_is_flagged_for_remeasure(tmp_path: Path):
    _task(tmp_path, "task-0003", None)
    rows = scoring_status(tmp_path)
    assert any("checks_v2" in e for e in rows[0].errors)


def test_dedupe_is_reported(tmp_path: Path):
    _task(
        tmp_path,
        "task-0004",
        [
            {"bucket": "hard", "name": "conduct", "gold": True},
            {"bucket": "hard", "name": "conduct", "gold": True},
            {"bucket": "l1", "name": "a", "gold": True},
        ],
    )
    rows = scoring_status(tmp_path)
    assert rows[0].n_recorded == 3 and rows[0].n_deduped == 2
    assert "1/3 check instances collapse" in render(rows)


def test_cross_bucket_name_collision_is_an_error(tmp_path: Path):
    """`retry_after_honored` is recorded as both l3 and soft in 4 real tasks;
    the two buckets carry different scoring, so which wins is arbitrary."""
    _task(
        tmp_path,
        "task-0005",
        [
            {"bucket": "l3", "name": "retry_after_honored", "gold": True},
            {"bucket": "soft", "name": "retry_after_honored", "gold": True},
        ],
    )
    rows = scoring_status(tmp_path)
    assert any("DIFFERENT scoring" in e for e in rows[0].errors)


def test_authored_values_in_the_sidecar_win_over_bucket_defaults(tmp_path: Path):
    """A migrated task must be able to show as done. Before the sidecar carried
    pass_value/mandatory, scoring-status could only reconstruct bucket defaults
    and every task read 'no mandatory checks' forever."""
    _task(
        tmp_path,
        "task-0031",
        [
            {
                "bucket": "l1",
                "name": "trap",
                "gold": True,
                "pass_value": 2,
                "fail_value": 0,
                "mandatory": True,
            },
            {
                "bucket": "l1",
                "name": "plumb",
                "gold": True,
                "pass_value": 0,
                "fail_value": -1,
                "mandatory": False,
            },
        ],
    )
    rows = scoring_status(tmp_path)
    assert rows[0].n_mandatory == 1
    assert rows[0].max_score == 2
    assert rows[0].ok, rows[0].errors

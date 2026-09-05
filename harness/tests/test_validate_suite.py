"""Unit tests for `bench validate-suite` (bench.commands.validate_suite) —
the suite-level uniqueness lint. Pure static analysis over tiny synthetic
task directories built under tmp_path; no Docker, no real tasks/ tree."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from bench.cli import _build_parser
from bench.commands.validate_suite import (
    canonicalize_scenario_name,
    jaccard,
    lint_suite,
)


def _write_task(
    tasks_dir: Path,
    task_id: str,
    *,
    vendor: str = "acme",
    scenarios: list[str] | None = None,
    primary_mechanic: str | None = None,
    entities: dict[str, str] | None = None,
    checks: list[str] | None = None,
    fixture_files: dict[str, Any] | None = None,
    fault_vars: list[str] | None = None,
) -> Path:
    task_dir = tasks_dir / task_id
    (task_dir / "verifier" / "scenarios").mkdir(parents=True, exist_ok=True)
    (task_dir / "verifier" / "fixtures").mkdir(parents=True, exist_ok=True)

    scenarios = scenarios if scenarios is not None else ["scenario_a"]
    entities = entities or {}

    task_yaml: dict[str, Any] = {
        "id": task_id,
        "category": "build",
        "vendor": vendor,
        "scenarios": scenarios,
        "entry": {"command": ["python", "-m", "x"]},
        "vendors": {
            vendor: {
                "image": f"{vendor}:local",
                "entities": {name: {"plural": plural} for name, plural in entities.items()},
            }
        },
        "contract": {
            "runtime": {
                "vendor_roles": {vendor: {"environment": {v: "1" for v in (fault_vars or [])}}}
            }
        },
    }
    if primary_mechanic is not None:
        task_yaml["primary_mechanic"] = primary_mechanic
    (task_dir / "task.yaml").write_text(yaml.safe_dump(task_yaml), encoding="utf-8")

    checks = checks if checks is not None else []
    lines = ["async def run(ctx):"]
    if checks:
        for name in checks:
            lines.append(f'    ctx.check_l1("{name}", True, "")')
    else:
        lines.append("    pass")
    (task_dir / "verifier" / "scenarios" / "main.py").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )

    for filename, content in (fixture_files or {}).items():
        (task_dir / "verifier" / "fixtures" / filename).write_text(
            json.dumps(content), encoding="utf-8"
        )

    return task_dir


def _write_mechanics(tmp_path: Path, slugs: dict[str, str]) -> Path:
    path = tmp_path / "mechanics.yaml"
    path.write_text(yaml.safe_dump(slugs), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# canonicalize_scenario_name / jaccard: pure-function unit tests
# ---------------------------------------------------------------------------


def test_canonicalize_backfill_synonyms():
    assert canonicalize_scenario_name("initial_sync") == "backfill"
    assert canonicalize_scenario_name("initial_backfill") == "backfill"
    assert canonicalize_scenario_name("full_backfill") == "backfill"
    assert canonicalize_scenario_name("v1_backfill_baseline") == "backfill"
    assert canonicalize_scenario_name("legacy_baseline_from_staffline") == "backfill"


def test_canonicalize_incremental_synonyms():
    assert canonicalize_scenario_name("incremental") == "incremental"
    assert canonicalize_scenario_name("incremental_catchup_on_placemint") == "incremental"
    assert canonicalize_scenario_name("poll_incremental") == "incremental"
    assert canonicalize_scenario_name("v2_incremental_watermark") == "incremental"


def test_canonicalize_substring_families():
    assert canonicalize_scenario_name("tamper_rejection.py") == "tamper"
    assert canonicalize_scenario_name("webhook_freshness") == "webhook_freshness"
    assert canonicalize_scenario_name("poll_reconcile_subjects") == "reconcile"
    assert canonicalize_scenario_name("dropped_delete_reconcile") == "reconcile"
    assert canonicalize_scenario_name("writeback_creates") == "writeback"
    assert canonicalize_scenario_name("idempotent_write_retries") == "writeback"


def test_canonicalize_unmatched_kept_as_is():
    assert canonicalize_scenario_name("conflict_refetch_retry") == "conflict_refetch_retry"
    assert canonicalize_scenario_name("bulk_fallback") == "bulk_fallback"


def test_jaccard_identical_and_disjoint():
    a = frozenset({"x", "y", "z"})
    assert jaccard(a, a) == 1.0
    assert jaccard(a, frozenset({"p", "q"})) == 0.0
    assert jaccard(frozenset(), frozenset()) == 0.0


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def test_validate_suite_registered_in_cli():
    parser = _build_parser()
    sub_actions = [a for a in parser._subparsers._group_actions if hasattr(a, "choices")]
    assert "validate-suite" in sub_actions[0].choices


def test_validate_suite_parses_flags():
    parser = _build_parser()
    args = parser.parse_args(
        ["validate-suite", "--tasks-dir", "tasks", "--enforce", "--json", "out.json"]
    )
    assert args.tasks_dir == "tasks"
    assert args.enforce is True
    assert args.json == "out.json"


def test_validate_suite_defaults():
    parser = _build_parser()
    args = parser.parse_args(["validate-suite"])
    assert args.tasks_dir == "tasks/public"
    assert args.enforce is False
    assert args.json is None


# ---------------------------------------------------------------------------
# lint_suite: synthetic task-dir scenarios
# ---------------------------------------------------------------------------


def test_undeclared_mechanic_warns(tmp_path):
    tasks_dir = tmp_path / "tasks"
    mechanics = _write_mechanics(tmp_path, {"foo_mechanic": "does foo"})
    _write_task(tasks_dir, "task-0001", scenarios=["scenario_a"])

    report = lint_suite(tasks_dir, mechanics_path=mechanics)

    assert any(
        f.level == "WARN" and f.rule == "undeclared_mechanic" and f.tasks == ["task-0001"]
        for f in report.findings
    )


def test_unknown_mechanic_fails(tmp_path):
    tasks_dir = tmp_path / "tasks"
    mechanics = _write_mechanics(tmp_path, {"foo_mechanic": "does foo"})
    _write_task(tasks_dir, "task-0001", scenarios=["scenario_a"], primary_mechanic="not_in_vocab")

    report = lint_suite(tasks_dir, mechanics_path=mechanics)

    assert any(
        f.level == "FAIL" and f.rule == "unknown_mechanic" and f.tasks == ["task-0001"]
        for f in report.findings
    )


def test_duplicate_mechanic_fails_both_tasks(tmp_path):
    tasks_dir = tmp_path / "tasks"
    mechanics = _write_mechanics(tmp_path, {"foo_mechanic": "does foo"})
    _write_task(
        tasks_dir,
        "task-0001",
        vendor="acme",
        scenarios=["scenario_a"],
        primary_mechanic="foo_mechanic",
    )
    _write_task(
        tasks_dir,
        "task-0002",
        vendor="globex",
        scenarios=["scenario_b"],
        primary_mechanic="foo_mechanic",
    )

    report = lint_suite(tasks_dir, mechanics_path=mechanics)

    dup = [f for f in report.findings if f.rule == "duplicate_mechanic"]
    assert len(dup) == 1
    assert dup[0].level == "FAIL"
    assert dup[0].tasks == ["task-0001", "task-0002"]


def test_empty_docker_copy_source_fails(tmp_path):
    tasks_dir = tmp_path / "tasks"
    mechanics = _write_mechanics(tmp_path, {"foo_mechanic": "does foo"})
    task_dir = _write_task(
        tasks_dir,
        "task-0001",
        scenarios=["scenario_a"],
        primary_mechanic="foo_mechanic",
    )
    repo = task_dir / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / "Dockerfile").write_text("FROM scratch\nCOPY tests /tests\n", encoding="utf-8")

    report = lint_suite(tasks_dir, mechanics_path=mechanics)

    assert any(
        finding.level == "FAIL"
        and finding.rule == "docker_copy_source_not_pristine"
        and finding.tasks == ["task-0001"]
        for finding in report.findings
    )


def test_scenario_shape_used_by_more_than_three_fails(tmp_path):
    tasks_dir = tmp_path / "tasks"
    mechanics = _write_mechanics(tmp_path, {})
    task_ids = []
    for i in range(4):
        task_id = f"task-000{i + 1}"
        task_ids.append(task_id)
        _write_task(
            tasks_dir, task_id, vendor=f"vendor{i}", scenarios=["initial_sync", "incremental"]
        )

    report = lint_suite(tasks_dir, mechanics_path=mechanics)

    shape_findings = [f for f in report.findings if f.rule == "scenario_shape_overused"]
    assert len(shape_findings) == 1
    assert shape_findings[0].level == "FAIL"
    assert shape_findings[0].tasks == sorted(task_ids)


def test_scenario_shape_used_by_three_or_fewer_does_not_fail(tmp_path):
    tasks_dir = tmp_path / "tasks"
    mechanics = _write_mechanics(tmp_path, {})
    for i in range(3):
        _write_task(
            tasks_dir,
            f"task-000{i + 1}",
            vendor=f"vendor{i}",
            scenarios=["initial_sync", "incremental"],
        )

    report = lint_suite(tasks_dir, mechanics_path=mechanics)

    assert not any(f.rule == "scenario_shape_overused" for f in report.findings)


def test_check_name_jaccard_identical_pair_fails_and_clean_pair_does_not(tmp_path):
    tasks_dir = tmp_path / "tasks"
    mechanics = _write_mechanics(tmp_path, {})
    _write_task(
        tasks_dir,
        "task-0001",
        vendor="acme",
        scenarios=["scenario_x"],
        checks=["alpha_check", "beta_check", "gamma_check"],
    )
    _write_task(
        tasks_dir,
        "task-0002",
        vendor="globex",
        scenarios=["scenario_y"],
        checks=["alpha_check", "beta_check", "gamma_check"],
    )
    _write_task(
        tasks_dir,
        "task-0003",
        vendor="initech",
        scenarios=["scenario_z"],
        checks=["zzz_one", "zzz_two", "zzz_three"],
    )

    report = lint_suite(tasks_dir, mechanics_path=mechanics)

    fail_jaccard = [f for f in report.findings if f.rule == "check_name_jaccard_high"]
    assert len(fail_jaccard) == 1
    assert fail_jaccard[0].tasks == ["task-0001", "task-0002"]

    # task-0003 shares nothing with either other task -- must not appear in
    # any Jaccard FAIL/WARN finding.
    jaccard_findings = [f for f in report.findings if f.rule.startswith("check_name_jaccard")]
    assert all("task-0003" not in f.tasks for f in jaccard_findings)

    # And the full pairwise list carries a 0.0 score for that pair.
    pair_scores = {frozenset((a, b)): score for a, b, score in report.jaccard_pairs}
    assert pair_scores[frozenset(("task-0001", "task-0003"))] == 0.0


def test_fixture_hash_identical_cross_vendor_fails(tmp_path):
    tasks_dir = tmp_path / "tasks"
    mechanics = _write_mechanics(tmp_path, {})
    shared_fixture = {"rows": [{"id": 1, "name": "a"}]}
    _write_task(
        tasks_dir,
        "task-0001",
        vendor="acme",
        scenarios=["scenario_x"],
        fixture_files={"seed.json": shared_fixture},
    )
    _write_task(
        tasks_dir,
        "task-0002",
        vendor="globex",
        scenarios=["scenario_y"],
        fixture_files={"seed.json": shared_fixture},
    )

    report = lint_suite(tasks_dir, mechanics_path=mechanics)

    fail = [f for f in report.findings if f.rule == "duplicate_fixture_cross_vendor"]
    assert len(fail) == 1
    assert fail[0].level == "FAIL"
    assert fail[0].tasks == ["task-0001", "task-0002"]


def test_fixture_hash_identical_same_vendor_is_info_not_fail(tmp_path):
    tasks_dir = tmp_path / "tasks"
    mechanics = _write_mechanics(tmp_path, {})
    shared_fixture = {"rows": [{"id": 1, "name": "a"}]}
    _write_task(
        tasks_dir,
        "task-0001",
        vendor="acme",
        scenarios=["scenario_x"],
        fixture_files={"seed.json": shared_fixture},
    )
    _write_task(
        tasks_dir,
        "task-0002",
        vendor="acme",
        scenarios=["scenario_y"],
        fixture_files={"seed.json": shared_fixture},
    )

    report = lint_suite(tasks_dir, mechanics_path=mechanics)

    assert not any(f.rule == "duplicate_fixture_cross_vendor" for f in report.findings)
    info = [f for f in report.findings if f.rule == "byte_clone_fixture"]
    assert len(info) == 1
    assert info[0].level == "INFO"
    assert info[0].tasks == ["task-0001", "task-0002"]


def test_render_and_to_dict_do_not_error(tmp_path):
    tasks_dir = tmp_path / "tasks"
    mechanics = _write_mechanics(tmp_path, {})
    _write_task(tasks_dir, "task-0001", scenarios=["scenario_a"], checks=["a_check"])

    report = lint_suite(tasks_dir, mechanics_path=mechanics)

    rendered = report.render()
    assert "Suite uniqueness lint" in rendered
    assert "Overall:" in rendered

    payload = report.to_dict()
    assert payload["task_count"] == 1
    assert isinstance(payload["findings"], list)

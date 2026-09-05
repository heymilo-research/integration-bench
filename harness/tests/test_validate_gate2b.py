"""Unit tests for the gauntlet's do-nothing-floor hardening in
bench.commands.validate (check_fraction, gate_2b_passes, the empty-baseline
sidecar, drift detection, and validate_task's wiring of all of it).

Pure unit tests only: grade_once is mocked/stubbed everywhere here — it needs
Docker, and this suite must never invoke it (see harness/CLAUDE.md-adjacent
constraint: a real measurement sweep is running against the same Docker
daemon on this machine)."""

import json
from pathlib import Path

import pytest

import bench.commands.validate as validate_mod
from bench.commands.grading_core import GradeResult
from bench.commands.validate import (
    baseline_artifact_drift,
    ValidateReport,
    baseline_drift,
    build_baseline_sidecar,
    check_fraction,
    empty_probe_is_red,
    gate_2b_passes,
    read_baseline_sidecar,
    validate_task,
    write_baseline_sidecar,
)
from bench.verdict import Check, Verdict


def _verdict(task, run_id, *, l1=None, hard=None, soft=None, l3=None) -> Verdict:
    return Verdict(
        task=task, run_id=run_id, l1=l1 or [], hard=hard or [], soft=soft or [], l3=l3 or []
    )


# ---------------------------------------------------------------------------
# check_fraction
# ---------------------------------------------------------------------------


def test_check_fraction_basic_ratio_with_no_override():
    v = _verdict(
        "t", "r1", l1=[Check("a", True), Check("b", False)], hard=[Check("c", True)]
    ).to_dict()
    assert check_fraction(v, None) == pytest.approx(2 / 3)


def test_check_fraction_counts_all_four_sections():
    v = _verdict(
        "t",
        "r1",
        l1=[Check("a", True)],
        hard=[Check("b", True)],
        soft=[Check("c", True), Check("d", False)],
        l3=[Check("e", True)],
    ).to_dict()
    assert check_fraction(v, None) == pytest.approx(4 / 5)


def test_check_fraction_uses_override_denominator():
    v = _verdict("t", "r1", l1=[Check("a", True)]).to_dict()  # 1 recorded check, passed
    assert check_fraction(v, 4) == pytest.approx(0.25)


def test_check_fraction_caps_at_one_when_override_denominator_smaller():
    v = _verdict("t", "r1", l1=[Check("a", True), Check("b", True), Check("c", True)]).to_dict()
    assert check_fraction(v, 1) == 1.0


def test_check_fraction_zero_when_denominator_is_zero():
    v = _verdict("t", "r1").to_dict()  # no checks recorded at all
    assert check_fraction(v, None) == 0.0
    assert check_fraction(v, 0) == 0.0


# ---------------------------------------------------------------------------
# gate_2b_passes
#
# Gold is no longer assumed to reach fraction 1.0 (the audit sweep found gold
# solutions legitimately missing a soft conduct check on several tasks) — the
# gate now requires a genuine measurement range (floor_fraction < gold_fraction)
# rather than pinning gold at a hardcoded ceiling.
# ---------------------------------------------------------------------------


def test_empty_red_uses_completion_policy_not_only_l1_failures():
    verdict = {
        "resolved": False,
        "l1": [{"name": "rejection_control", "ok": True}],
        "l3": [{"name": "business_effect", "ok": False}],
    }
    assert empty_probe_is_red(verdict) is True
    assert empty_probe_is_red({**verdict, "resolved": True}) is False


def test_gate_2b_passes_when_gold_perfect_and_floor_strictly_below_it():
    assert gate_2b_passes(empty_red=True, floor_fraction=0.5, gold_fraction=1.0) is True


def test_gate_2b_passes_when_gold_fraction_is_measured_below_one():
    # Realistic post-sweep case: gold measures 0.979 (misses one soft check
    # out of ~48), floor measures 0.702 — still a genuine measurement range.
    assert gate_2b_passes(empty_red=True, floor_fraction=0.702, gold_fraction=0.979) is True


def test_gate_2b_fails_when_empty_patch_is_resolved():
    assert gate_2b_passes(empty_red=False, floor_fraction=0.0, gold_fraction=1.0) is False


def test_gate_2b_fails_when_floor_at_or_above_measured_gold_fraction():
    # floor strictly above gold -> definitely no range.
    assert gate_2b_passes(empty_red=True, floor_fraction=0.98, gold_fraction=0.979) is False
    # floor exactly equal to gold -> still no range (boundary is exclusive).
    assert gate_2b_passes(empty_red=True, floor_fraction=0.979, gold_fraction=0.979) is False


def test_gate_2b_fails_when_floor_reaches_the_ceiling():
    # A do-nothing probe scoring exactly what gold scores means the dense
    # reward can never distinguish do-nothing from a correct solution.
    assert gate_2b_passes(empty_red=True, floor_fraction=1.0, gold_fraction=1.0) is False


# ---------------------------------------------------------------------------
# empty-baseline sidecar: shape, write/read roundtrip, drift
# ---------------------------------------------------------------------------


def test_build_baseline_sidecar_shape_and_floor_is_max_of_probes():
    checks = {"l1": ["a"], "hard": ["b"], "l3": [], "soft": ["c"]}
    payload = build_baseline_sidecar(
        n_total=3, empty_fraction=0.2, stub_fraction=0.5, gold_fraction=0.979, checks=checks
    )
    assert payload["schema"] == 2
    assert payload["n_total"] == 3
    assert payload["floor_fraction"] == pytest.approx(0.5)
    assert payload["gold_fraction"] == pytest.approx(0.979)
    assert payload["probes"] == {"empty": 0.2, "stub": 0.5}
    assert payload["checks"] == checks
    assert payload["generated"].startswith("bench validate --write-baseline ")


def test_build_baseline_sidecar_carries_measured_gold_fraction_below_one():
    # Gold is no longer assumed to be 1.0 — the sidecar must carry whatever
    # was actually measured (e.g. 47/48 checks -> a missed soft check).
    payload = build_baseline_sidecar(
        n_total=48,
        empty_fraction=0.702,
        stub_fraction=0.6,
        gold_fraction=47 / 48,
        checks={"l1": [], "hard": [], "l3": [], "soft": []},
    )
    assert payload["gold_fraction"] == pytest.approx(47 / 48)


def test_build_baseline_sidecar_floor_rests_on_empty_alone_when_stub_skipped():
    checks = {"l1": [], "hard": [], "l3": [], "soft": []}
    payload = build_baseline_sidecar(
        n_total=2, empty_fraction=0.5, stub_fraction=None, gold_fraction=1.0, checks=checks
    )
    assert payload["floor_fraction"] == pytest.approx(0.5)
    assert payload["probes"]["stub"] is None


def test_write_and_read_baseline_sidecar_roundtrip(tmp_path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    payload = build_baseline_sidecar(
        n_total=1,
        empty_fraction=0.0,
        stub_fraction=0.0,
        gold_fraction=1.0,
        checks={"l1": ["x"], "hard": [], "l3": [], "soft": []},
    )
    path = write_baseline_sidecar(task_dir, payload)
    assert path == task_dir / "verifier" / "empty-baseline.json"
    assert path.is_file()
    assert read_baseline_sidecar(task_dir) == payload


def test_read_baseline_sidecar_none_when_absent(tmp_path):
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    assert read_baseline_sidecar(task_dir) is None


def test_baseline_drift_detects_changed_probe():
    existing = {"probes": {"empty": 0.5, "stub": 0.5}}
    assert baseline_drift(existing, {"empty": 0.5, "stub": 0.6}) == ["stub"]


def test_baseline_drift_empty_when_probes_match():
    existing = {"probes": {"empty": 0.5, "stub": 0.5}}
    assert baseline_drift(existing, {"empty": 0.5, "stub": 0.5}) == []


def test_baseline_drift_ignores_none_probe_this_run():
    # e.g. the entry command's shape changed and the stub probe is now skipped.
    existing = {"probes": {"empty": 0.5, "stub": 0.5}}
    assert baseline_drift(existing, {"empty": 0.5, "stub": None}) == []


def test_baseline_drift_ignores_probe_absent_from_stored_sidecar():
    existing = {"probes": {"empty": 0.5}}  # older sidecar, no stub key ever recorded
    assert baseline_drift(existing, {"empty": 0.5, "stub": 0.5}) == []


def test_baseline_drift_detects_changed_gold_fraction():
    existing = {"probes": {"empty": 0.5, "stub": 0.5}, "gold_fraction": 1.0}
    drifted = baseline_drift(existing, {"empty": 0.5, "stub": 0.5, "gold_fraction": 47 / 48})
    assert drifted == ["gold_fraction"]


def test_baseline_drift_gold_fraction_matches_is_not_drift():
    existing = {"probes": {"empty": 0.5, "stub": 0.5}, "gold_fraction": 47 / 48}
    assert baseline_drift(existing, {"empty": 0.5, "stub": 0.5, "gold_fraction": 47 / 48}) == []


def test_baseline_drift_ignores_gold_fraction_absent_from_older_sidecar():
    existing = {"probes": {"empty": 0.5, "stub": 0.5}}  # pre-gold_fraction schema
    assert baseline_drift(existing, {"empty": 0.5, "stub": 0.5, "gold_fraction": 1.0}) == []


def test_baseline_artifact_drift_is_exact_and_fail_closed():
    current = {
        "n_total": 1,
        "checks": {"l1": ["a"], "hard": [], "soft": [], "l3": []},
        "checks_v2": [{"name": "a", "gold": True, "mandatory": True}],
        "completion": {"mode": "authored_mandatory_checks", "required_checks": ["a"]},
        "scoring": {"version": "task-score-v3-fixed-gold", "floor_task_score": 0.0},
    }
    assert baseline_artifact_drift(dict(current), current) == []
    renamed = json.loads(json.dumps(current))
    renamed["checks_v2"][0]["name"] = "b"
    assert baseline_artifact_drift(renamed, current) == ["checks_v2"]
    missing = dict(current)
    del missing["completion"]
    assert baseline_artifact_drift(missing, current) == ["completion:missing"]


# ---------------------------------------------------------------------------
# validate_task wiring: grade_once and stub_patch_text are mocked/stubbed.
# ---------------------------------------------------------------------------


def _patch_kind(patch_path: Path) -> str:
    name = Path(patch_path).name
    if name.endswith(".empty.patch"):
        return "empty"
    if name.endswith(".stub.patch"):
        return "stub"
    return "gold"


def _fake_grade_once(gold, empty, *, stub_default=None, stub_by_run=None):
    """Build a grade_once stand-in that returns canned Verdicts by probe type,
    stamping each with the run_id it was called with (mirrors real
    grade_once's per-run_id verdict)."""
    stub_by_run = stub_by_run or {}

    def fake(
        task_dir, patch_path, run_id, *, keep=False, workdir_root=None, startup_timeout_s=120.0
    ):
        kind = _patch_kind(patch_path)
        if kind == "gold":
            v = gold
        elif kind == "empty":
            v = empty
        else:
            idx = int(run_id.rsplit("-", 1)[-1])
            v = stub_by_run.get(idx, stub_default)
        d = v.to_dict()
        d["run_id"] = run_id
        return GradeResult(verdict=Verdict.from_dict(d), workspace_dir=Path("unused"), stack=None)

    return fake


def _task_with_solution(tmp_path) -> Path:
    task_dir = tmp_path / "task"
    (task_dir / "authoring").mkdir(parents=True)
    (task_dir / "authoring" / "solution.patch").write_text(
        "# placeholder; grade_once is mocked in these tests\n"
    )
    return task_dir


def test_validate_task_full_pass_computes_fractions_and_gate_2b(tmp_path, monkeypatch):
    task_dir = _task_with_solution(tmp_path)
    monkeypatch.setattr(validate_mod, "stub_patch_text", lambda task_dir: "diff --git a/x b/x\n")

    gold = _verdict(
        "task-x",
        "gold",
        l1=[Check("a", True)],
        hard=[Check("b", True)],
        soft=[Check("c", True)],
        l3=[Check("d", True)],
    )
    empty = _verdict(
        "task-x",
        "empty",
        l1=[Check("a", False)],
        hard=[Check("b", True)],
        soft=[Check("c", True)],
        l3=[],
    )
    stub = _verdict(
        "task-x",
        "stub",
        l1=[Check("a", False)],
        hard=[Check("b", True)],
        soft=[Check("c", True)],
        l3=[],
    )
    monkeypatch.setattr(
        validate_mod, "grade_once", _fake_grade_once(gold, empty, stub_default=stub)
    )

    report = validate_task(task_dir, runs=3)

    assert isinstance(report, ValidateReport)
    assert report.gold_green is True
    assert report.empty_red is True
    assert report.flake_gate is True
    assert report.stub_skipped is False
    assert report.n_total == 4
    assert report.gold_fraction == 1.0
    assert report.empty_fraction == pytest.approx(0.5)
    assert report.stub_fraction == pytest.approx(0.5)
    assert report.floor_fraction == pytest.approx(0.5)
    assert report.gate_2b is True
    assert report.passed is True
    rendered = report.render()
    assert "gate 2b" in rendered
    assert "Overall: PASS" in rendered


def test_strict_validate_preserves_zero_check_probe_error(tmp_path, monkeypatch):
    task_dir = _task_with_solution(tmp_path)
    monkeypatch.setattr(validate_mod, "stub_patch_text", lambda _task_dir: None)

    def fake(_task_dir, _patch_path, run_id, **_kwargs):
        verdict = Verdict.error_verdict(
            "task-x", run_id, "command failed (125): docker compose unavailable"
        )
        return GradeResult(verdict=verdict, workspace_dir=Path("unused"), stack=None)

    monkeypatch.setattr(validate_mod, "grade_once", fake)
    report = validate_task(task_dir, runs=1, strict=True)

    assert report.passed is False
    assert report.strict_gate is False
    assert "no positive gold ceiling" in report.strict_rationale
    rendered = report.render()
    assert "probe error [gold]" in rendered
    assert "docker compose unavailable" in rendered
    assert "Overall: FAIL" in rendered


def test_probe_error_is_not_masked_by_task_score_policy(tmp_path, monkeypatch):
    task_dir = _task_with_solution(tmp_path)
    verifier_dir = task_dir / "verifier"
    verifier_dir.mkdir()
    (verifier_dir / "task-score-policy.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "mode": "positive-check-allowlist",
                "positive_checks": ["required-check"],
                "mandatory_checks": ["required-check"],
            }
        )
    )
    monkeypatch.setattr(validate_mod, "stub_patch_text", lambda _task_dir: None)

    def fake(_task_dir, _patch_path, run_id, **_kwargs):
        verdict = Verdict.error_verdict(
            "task-x", run_id, "candidate image build could not resolve package index"
        )
        return GradeResult(
            verdict=verdict,
            workspace_dir=Path("unused"),
            stack=None,
            failure_class="candidate_build_failure",
        )

    monkeypatch.setattr(validate_mod, "grade_once", fake)
    report = validate_task(task_dir, runs=1, strict=True)

    assert report.passed is False
    rendered = report.render()
    assert "probe error [gold]" in rendered
    assert "could not resolve package index" in rendered


def test_validate_retries_only_benchmark_infrastructure_failure(tmp_path, monkeypatch):
    task_dir = _task_with_solution(tmp_path)
    monkeypatch.setattr(validate_mod, "stub_patch_text", lambda _task_dir: None)
    gold = _verdict("task-x", "gold", l1=[Check("a", True)])
    empty = _verdict("task-x", "empty", l1=[Check("a", False)])
    calls: dict[str, int] = {"gold": 0, "empty": 0}

    def fake(_task_dir, patch_path, run_id, **_kwargs):
        kind = _patch_kind(patch_path)
        calls[kind] += 1
        if kind == "empty" and calls[kind] == 1:
            verdict = Verdict.error_verdict("task-x", run_id, "transient compose startup")
            return GradeResult(
                verdict=verdict,
                workspace_dir=Path("unused"),
                stack=None,
                failure_class="benchmark_infrastructure_failure",
            )
        source = gold if kind == "gold" else empty
        data = source.to_dict()
        data["run_id"] = run_id
        return GradeResult(
            verdict=Verdict.from_dict(data), workspace_dir=Path("unused"), stack=None
        )

    monkeypatch.setattr(validate_mod, "grade_once", fake)
    report = validate_task(task_dir, runs=1)

    assert report.passed is True
    assert calls == {"gold": 1, "empty": 2}


def test_validate_does_not_retry_candidate_failure(tmp_path, monkeypatch):
    task_dir = _task_with_solution(tmp_path)
    monkeypatch.setattr(validate_mod, "stub_patch_text", lambda _task_dir: None)
    calls = 0

    def fake(_task_dir, _patch_path, run_id, **_kwargs):
        nonlocal calls
        calls += 1
        return GradeResult(
            verdict=Verdict.error_verdict("task-x", run_id, "candidate build failed"),
            workspace_dir=Path("unused"),
            stack=None,
            failure_class="candidate_build_failure",
        )

    monkeypatch.setattr(validate_mod, "grade_once", fake)
    report = validate_task(task_dir, runs=1)

    assert report.passed is False
    assert calls == 2  # one gold and one empty probe; neither is retried


def test_validate_task_stub_skipped_reports_and_gates_vacuously(tmp_path, monkeypatch):
    task_dir = _task_with_solution(tmp_path)
    monkeypatch.setattr(validate_mod, "stub_patch_text", lambda task_dir: None)

    gold = _verdict("task-x", "gold", l1=[Check("a", True)], hard=[Check("b", True)])
    empty = _verdict("task-x", "empty", l1=[Check("a", False)], hard=[Check("b", True)])
    monkeypatch.setattr(validate_mod, "grade_once", _fake_grade_once(gold, empty))

    report = validate_task(task_dir, runs=2)

    assert report.stub_skipped is True
    assert report.stub_verdicts == []
    assert report.stub_fraction is None
    assert report.floor_fraction == report.empty_fraction
    assert report.gate_2b is True
    assert "stub: skipped (entry shape)" in report.render()


def test_validate_task_gate_2b_catches_stub_reaching_full_floor(tmp_path, monkeypatch):
    """The regression this whole gate exists for: a stub that runs to
    completion can bank checks the empty patch can't (e.g. prohibitions that
    pass vacuously). The OLD gate (empty_red alone) is blind to this; gate 2b
    must not be."""
    task_dir = _task_with_solution(tmp_path)
    monkeypatch.setattr(validate_mod, "stub_patch_text", lambda task_dir: "diff --git a/x b/x\n")

    gold = _verdict("task-x", "gold", l1=[Check("a", True)], hard=[Check("b", True)])
    empty = _verdict("task-x", "empty", l1=[Check("a", False)], hard=[Check("b", True)])
    stub = _verdict(
        "task-x", "stub", l1=[Check("a", True)], hard=[Check("b", True)]
    )  # banks everything
    monkeypatch.setattr(
        validate_mod, "grade_once", _fake_grade_once(gold, empty, stub_default=stub)
    )

    report = validate_task(task_dir, runs=2)

    assert report.empty_red is True  # the pre-existing gate would have passed
    assert report.stub_fraction == 1.0
    assert report.floor_fraction == 1.0
    assert report.gate_2b is False  # the strengthened gate catches it
    assert report.passed is False


def test_validate_task_gate_2b_passes_with_measured_gold_fraction_below_one(tmp_path, monkeypatch):
    """Post-audit-sweep case: gold legitimately misses one soft conduct check
    (gold_fraction < 1.0) but there's still a genuine measurement range above
    the do-nothing floor, so the gate must pass — an author may still choose
    to go fix gold, but validation itself is not blocked on it."""
    task_dir = _task_with_solution(tmp_path)
    monkeypatch.setattr(validate_mod, "stub_patch_text", lambda task_dir: None)

    soft = [Check(f"s{i}", True) for i in range(9)] + [Check("s9", False)]  # gold misses one
    gold = _verdict("task-x", "gold", l1=[Check("a", True)], hard=[Check("b", True)], soft=soft)
    empty = _verdict("task-x", "empty", l1=[Check("a", False)], hard=[Check("b", True)])
    monkeypatch.setattr(validate_mod, "grade_once", _fake_grade_once(gold, empty))

    report = validate_task(task_dir, runs=1)

    assert report.gold_green is True  # a soft miss never affects resolved
    assert report.n_total == 12
    assert report.gold_fraction == pytest.approx(11 / 12)
    assert report.floor_fraction < report.gold_fraction
    assert report.gate_2b is True
    assert report.passed is True
    rendered = report.render()
    assert f"gold={11 / 12:.3f}" in rendered
    assert "note: gold_fraction" in rendered  # informational, non-blocking


def test_validate_task_gate_2b_fails_when_floor_reaches_measured_gold_ceiling(
    tmp_path, monkeypatch
):
    """floor_fraction >= gold_fraction (rather than the old floor >= 1.0)
    is what must now fail the gate, since gold itself may not reach 1.0."""
    task_dir = _task_with_solution(tmp_path)
    monkeypatch.setattr(validate_mod, "stub_patch_text", lambda task_dir: None)

    gold = _verdict(
        "task-x",
        "gold",
        l1=[Check("a", True)],
        hard=[Check("b", True)],
        soft=[Check("c", True), Check("d", False)],
    )
    # Empty misses `a` (still L1-red) but happens to bank `d` (a soft check
    # that penalizes work gold actually does) -> same fraction as gold.
    empty = _verdict(
        "task-x",
        "empty",
        l1=[Check("a", False)],
        hard=[Check("b", True)],
        soft=[Check("c", True), Check("d", True)],
    )
    monkeypatch.setattr(validate_mod, "grade_once", _fake_grade_once(gold, empty))

    report = validate_task(task_dir, runs=1)

    assert report.gold_fraction == pytest.approx(0.75)
    assert report.floor_fraction == pytest.approx(0.75)
    assert report.gate_2b is False
    assert report.passed is False


def test_validate_task_flake_gate_extends_to_stub_verdicts(tmp_path, monkeypatch):
    task_dir = _task_with_solution(tmp_path)
    monkeypatch.setattr(validate_mod, "stub_patch_text", lambda task_dir: "diff --git a/x b/x\n")

    gold = _verdict("task-x", "gold", l1=[Check("a", True)])
    empty = _verdict("task-x", "empty", l1=[Check("a", False)])
    stub_run0 = _verdict("task-x", "stub0", l1=[Check("a", False)])
    stub_run1 = _verdict("task-x", "stub1", l1=[Check("a", True)])  # flaky, differs from run 0
    monkeypatch.setattr(
        validate_mod,
        "grade_once",
        _fake_grade_once(gold, empty, stub_by_run={0: stub_run0, 1: stub_run1}),
    )

    report = validate_task(task_dir, runs=2)

    assert report.flake_gate is False
    assert any("stub run 0 vs 1: $.l1[0].ok" in item for item in report.flake_drift)
    assert "drift: stub run 0 vs 1" in report.render()
    assert report.passed is False


def test_validate_task_write_baseline_writes_sidecar_on_pass(tmp_path, monkeypatch):
    task_dir = _task_with_solution(tmp_path)
    monkeypatch.setattr(validate_mod, "stub_patch_text", lambda task_dir: "diff --git a/x b/x\n")

    gold = _verdict("task-x", "gold", l1=[Check("a", True)], hard=[Check("b", True)])
    empty = _verdict("task-x", "empty", l1=[Check("a", False)], hard=[Check("b", True)])
    stub = _verdict("task-x", "stub", l1=[Check("a", False)], hard=[Check("b", True)])
    monkeypatch.setattr(
        validate_mod, "grade_once", _fake_grade_once(gold, empty, stub_default=stub)
    )

    report = validate_task(task_dir, runs=2, write_baseline=True)

    assert report.baseline_written is True
    sidecar_path = task_dir / "verifier" / "empty-baseline.json"
    assert sidecar_path.is_file()
    data = json.loads(sidecar_path.read_text())
    assert data["floor_fraction"] == pytest.approx(0.5)
    assert data["n_total"] == 2
    assert "sidecar written" in report.render()


def test_validate_task_write_baseline_skips_write_when_gauntlet_fails(tmp_path, monkeypatch):
    task_dir = _task_with_solution(tmp_path)
    monkeypatch.setattr(validate_mod, "stub_patch_text", lambda task_dir: None)

    gold = _verdict("task-x", "gold", l1=[Check("a", False)])  # gold not green
    empty = _verdict("task-x", "empty", l1=[Check("a", False)])
    monkeypatch.setattr(validate_mod, "grade_once", _fake_grade_once(gold, empty))

    report = validate_task(task_dir, runs=1, write_baseline=True)

    assert report.baseline_written is False
    assert not (task_dir / "verifier" / "empty-baseline.json").exists()


def test_validate_task_detects_drift_against_stored_sidecar(tmp_path, monkeypatch):
    task_dir = _task_with_solution(tmp_path)
    monkeypatch.setattr(validate_mod, "stub_patch_text", lambda task_dir: None)

    stale = build_baseline_sidecar(
        n_total=2,
        empty_fraction=0.0,
        stub_fraction=None,
        gold_fraction=1.0,
        checks={"l1": ["a"], "hard": ["b"], "l3": [], "soft": []},
    )
    write_baseline_sidecar(task_dir, stale)

    gold = _verdict("task-x", "gold", l1=[Check("a", True)], hard=[Check("b", True)])
    empty = _verdict(
        "task-x", "empty", l1=[Check("a", False)], hard=[Check("b", True)]
    )  # now 0.5, stored was 0.0
    monkeypatch.setattr(validate_mod, "grade_once", _fake_grade_once(gold, empty))

    report = validate_task(task_dir, runs=1, write_baseline=False)

    assert report.baseline_drift == [
        "empty",
        "checks_v2:missing",
        "completion:missing",
        "scoring:missing",
    ]
    assert report.passed is False
    assert "DRIFT" in report.render()


def test_validate_task_detects_drift_on_gold_fraction(tmp_path, monkeypatch):
    task_dir = _task_with_solution(tmp_path)
    monkeypatch.setattr(validate_mod, "stub_patch_text", lambda task_dir: None)

    # Stored sidecar assumed gold reached 1.0; re-measuring now finds gold
    # missing a soft check (gold_fraction 0.5 here) — that's drift too.
    stale = build_baseline_sidecar(
        n_total=2,
        empty_fraction=0.0,
        stub_fraction=None,
        gold_fraction=1.0,
        checks={"l1": ["a"], "hard": ["b"], "l3": [], "soft": []},
    )
    write_baseline_sidecar(task_dir, stale)

    gold = _verdict(
        "task-x", "gold", l1=[Check("a", True)], hard=[Check("b", False)]
    )  # now 0.5, stored was 1.0
    empty = _verdict("task-x", "empty", l1=[Check("a", False)], hard=[Check("b", False)])
    monkeypatch.setattr(validate_mod, "grade_once", _fake_grade_once(gold, empty))

    report = validate_task(task_dir, runs=1, write_baseline=False)

    assert report.baseline_drift == [
        "gold_fraction",
        "checks_v2:missing",
        "completion:missing",
        "scoring:missing",
    ]
    assert report.passed is False


def test_validate_task_legacy_sidecar_is_incomplete_even_when_fractions_match(
    tmp_path, monkeypatch
):
    task_dir = _task_with_solution(tmp_path)
    monkeypatch.setattr(validate_mod, "stub_patch_text", lambda task_dir: None)

    matching = build_baseline_sidecar(
        n_total=2,
        empty_fraction=0.5,
        stub_fraction=None,
        gold_fraction=1.0,
        checks={"l1": ["a"], "hard": ["b"], "l3": [], "soft": []},
    )
    write_baseline_sidecar(task_dir, matching)

    gold = _verdict("task-x", "gold", l1=[Check("a", True)], hard=[Check("b", True)])
    empty = _verdict("task-x", "empty", l1=[Check("a", False)], hard=[Check("b", True)])
    monkeypatch.setattr(validate_mod, "grade_once", _fake_grade_once(gold, empty))

    report = validate_task(task_dir, runs=1, write_baseline=False)

    assert report.baseline_drift == ["checks_v2:missing", "completion:missing", "scoring:missing"]
    assert report.passed is False


def test_strict_gate_fails_high_floor(tmp_path, monkeypatch):
    """floor 0.5 > 0.40*gold: strict run fails, non-strict run passes."""
    task_dir = _task_with_solution(tmp_path)
    monkeypatch.setattr(validate_mod, "stub_patch_text", lambda task_dir: None)

    gold = _verdict(
        "task-x",
        "gold",
        l1=[Check("a", True), Check("c", True)],
        hard=[Check("b", True), Check("d", True)],
    )
    empty = _verdict(
        "task-x",
        "empty",
        l1=[Check("a", False), Check("c", True)],
        hard=[Check("b", True), Check("d", False)],
    )
    monkeypatch.setattr(validate_mod, "grade_once", _fake_grade_once(gold, empty))

    lax = validate_task(task_dir, runs=1, write_baseline=True)
    assert lax.passed is True
    assert lax.strict_gate is None

    strict = validate_task(task_dir, runs=1, write_baseline=True, strict=True)
    assert strict.floor_fraction == 0.5
    assert strict.strict_gate is False
    assert strict.passed is False


def test_strict_gate_fails_when_stub_probe_skipped(tmp_path, monkeypatch):
    """A skipped stub probe cannot certify a floor, however low `empty` was.

    `floor` is defined as max(empty, stub); with the stub unmeasured it is only
    an empty-only lower bound. The 200-task holdout expansion regressed exactly
    here — `entry.command: [python, main.py]` is not stub-patchable, so the
    probe skipped 200/200 while the floor still read as certified.
    """
    task_dir = _task_with_solution(tmp_path)
    monkeypatch.setattr(validate_mod, "stub_patch_text", lambda task_dir: None)

    gold = _verdict(
        "task-x",
        "gold",
        l1=[Check("a", True), Check("c", True), Check("e", True)],
        hard=[Check("b", True)],
    )
    empty = _verdict(
        "task-x",
        "empty",
        l1=[Check("a", False), Check("c", False), Check("e", False)],
        hard=[Check("b", True)],
    )
    monkeypatch.setattr(validate_mod, "grade_once", _fake_grade_once(gold, empty))

    report = validate_task(task_dir, runs=1, write_baseline=True, strict=True)
    assert report.floor_fraction == 0.25
    assert report.stub_skipped is True
    assert report.strict_gate is False
    assert "stub probe SKIPPED" in report.strict_rationale
    assert report.passed is False


def test_strict_gate_passes_low_floor_with_measured_stub(tmp_path, monkeypatch):
    """The low-floor pass path, with the stub probe actually measured.

    Same fractions as the skipped-stub case above; the only difference is that
    `stub` is a real number, so max(empty, stub) is a real floor.
    """
    task_dir = _task_with_solution(tmp_path)
    monkeypatch.setattr(validate_mod, "stub_patch_text", lambda task_dir: "stub-patch")

    gold = _verdict(
        "task-x",
        "gold",
        l1=[Check("a", True), Check("c", True), Check("e", True)],
        hard=[Check("b", True)],
    )
    empty = _verdict(
        "task-x",
        "empty",
        l1=[Check("a", False), Check("c", False), Check("e", False)],
        hard=[Check("b", True)],
    )
    stub = _verdict(
        "task-x",
        "stub",
        l1=[Check("a", False), Check("c", False), Check("e", False)],
        hard=[Check("b", True)],
    )
    monkeypatch.setattr(
        validate_mod, "grade_once", _fake_grade_once(gold, empty, stub_default=stub)
    )

    report = validate_task(task_dir, runs=1, write_baseline=True, strict=True)
    assert report.floor_fraction == 0.25
    assert report.stub_skipped is False
    assert report.strict_gate is True
    assert report.passed is True

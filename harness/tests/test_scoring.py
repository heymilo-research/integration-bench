"""Tests for the per-test scoring model (TODO "Decided — scoring, 2026-08-07").

The probe invariants (gold=100/solved, empty=0/unsolved, stub=0/unsolved) are
the audit bar, so they are asserted directly rather than left to task-level
gauntlet runs.
"""

from __future__ import annotations

import pytest
import json

from bench.scoring import (
    ScoringError,
    bench_score,
    max_score,
    raw_score,
    solve_rate,
    solved,
    task_score,
    validate_scoring,
    wilson_interval,
    gold_from_sidecar,
)
from bench.verdict import Check, Verdict
from bench.verifier.checks import CheckRecorder


def _v(*checks: Check, error: str | None = None) -> Verdict:
    return Verdict(task="task-0001", run_id="r", l1=list(checks), error=error)


def _c(name, ok, pv=1, fv=0, mandatory=False) -> Check:
    return Check(name=name, ok=ok, pass_value=pv, fail_value=fv, mandatory=mandatory)


# --- Check semantics ---------------------------------------------------------


def test_value_is_pass_or_fail_value():
    assert _c("a", True, pv=2, fv=0).value == 2
    assert _c("a", False, pv=2, fv=0).value == 0
    assert _c("a", False, pv=0, fv=-1).value == -1


def test_illegal_value_rejected():
    """The value set is closed at {-1,0,1,2}; widening it silently would make
    scores across tasks incomparable."""
    with pytest.raises(ValueError, match="pass_value"):
        Check(name="a", ok=True, pass_value=3)
    with pytest.raises(ValueError, match="fail_value"):
        Check(name="a", ok=True, fail_value=-2)


def test_schema1_verdict_still_loads():
    """Back-compat: a check written before this change has no scoring fields."""
    c = Check.from_dict({"name": "a", "ok": True, "detail": "d"})
    assert (c.pass_value, c.fail_value, c.mandatory) == (1, 0, False)


def test_value_is_denormalised_into_the_dict():
    """So a captured verdict is re-scorable without its scenario source."""
    assert Check(name="a", ok=True, pass_value=2).to_dict()["value"] == 2


def test_gold_sidecar_requires_explicit_authored_mandatory_subset(tmp_path):
    task = tmp_path / "task-0001"
    verifier = task / "verifier"
    verifier.mkdir(parents=True)
    payload = {
        "checks_v2": [
            {
                "bucket": "l1",
                "name": "a",
                "gold": True,
                "pass_value": 1,
                "fail_value": 0,
                "mandatory": True,
            },
        ],
        "completion": {
            "mode": "authored_mandatory_checks",
            "required_checks": ["a"],
        },
    }
    (verifier / "empty-baseline.json").write_text(json.dumps(payload))
    gold = gold_from_sidecar(task)
    assert gold.l1[0].mandatory is True


def test_gold_sidecar_rejects_unexplained_omission(tmp_path):
    task = tmp_path / "task-0001"
    verifier = task / "verifier"
    verifier.mkdir(parents=True)
    payload = {
        "checks_v2": [{"bucket": "l1", "name": "a", "gold": True, "mandatory": True}],
        "completion": {"mode": "authored_mandatory_checks", "required_checks": []},
    }
    (verifier / "empty-baseline.json").write_text(json.dumps(payload))
    with pytest.raises(ScoringError, match="authored mandatory"):
        gold_from_sidecar(task)


# --- the formulas ------------------------------------------------------------


def test_max_score_counts_only_positive_gold_value():
    gold = _v(_c("build", True, pv=2), _c("preserve", True, pv=0, fv=-1))
    assert max_score(gold) == 2  # the preserve check cannot raise the ceiling


def test_gold_scores_100_and_is_solved():
    gold = _v(_c("trap", True, pv=2, mandatory=True), _c("plumbing", True, pv=1))
    assert task_score(gold, gold) == 100.0
    assert solved(gold, gold) is True


def test_empty_and_stub_score_zero_and_unsolved():
    gold = _v(_c("trap", True, pv=2, mandatory=True), _c("plumbing", True, pv=1))
    empty = _v(_c("trap", False, pv=2, mandatory=True), _c("plumbing", False, pv=1))
    assert task_score(empty, gold) == 0.0
    assert solved(empty, gold) is False


def test_mandatory_failure_zeros_otherwise_earned_plumbing_credit():
    gold = _v(_c("core", True, mandatory=True), _c("plumbing", True))
    unchanged = _v(_c("core", False, mandatory=True), _c("plumbing", True))
    assert raw_score(unchanged, gold) == 1
    assert task_score(unchanged, gold) == 0.0


def test_negative_total_clamps_to_zero():
    """A submission that only breaks things scores 0, never below."""
    gold = _v(_c("build", True, pv=2), _c("preserve", True, pv=0, fv=-1))
    regressed = _v(_c("build", False, pv=2), _c("preserve", False, pv=0, fv=-1))
    assert raw_score(regressed) == -1
    assert task_score(regressed, gold) == 0.0


def test_partial_credit_is_proportional():
    gold = _v(_c("a", True, pv=2), _c("b", True, pv=1), _c("c", True, pv=1))
    partial = _v(_c("a", True, pv=2), _c("b", False, pv=1), _c("c", False, pv=1))
    assert task_score(partial, gold) == pytest.approx(50.0)


def test_unreached_checks_use_authored_failure_value():
    """A crash cannot shrink its own denominator: max_score comes from GOLD, so
    checks the run never reached are simply absent and earn nothing."""
    gold = _v(_c("a", True, pv=1), _c("b", True, pv=1), _c("c", True, pv=1))
    crashed = _v(_c("a", True, pv=1))  # b and c never ran
    assert task_score(crashed, gold) == pytest.approx(100.0 / 3)


def test_max_score_zero_raises_rather_than_scoring_zero():
    """A task with nothing positive on gold is broken, not hard. Returning 0.0
    here would make it indistinguishable from a task every model fails."""
    gold = _v(_c("preserve", True, pv=0, fv=-1))
    with pytest.raises(ScoringError, match="max_score"):
        task_score(gold, gold)


# --- Solved ------------------------------------------------------------------


def test_solved_requires_every_mandatory_check():
    gold = _v(_c("m1", True, mandatory=True), _c("m2", True, mandatory=True))
    assert solved(gold, gold) is True
    assert (
        solved(_v(_c("m1", True, mandatory=True), _c("m2", False, mandatory=True)), gold) is False
    )


def test_solved_requires_missing_gold_mandatory_check_to_be_present():
    gold = _v(_c("m1", True, mandatory=True), _c("m2", True, mandatory=True))
    assert solved(_v(_c("m1", True, mandatory=True)), gold) is False


def test_non_mandatory_failures_do_not_block_solved():
    verdict = _v(_c("m", True, mandatory=True), _c("cosmetic", False, pv=0, fv=0))
    assert (
        solved(verdict, _v(_c("m", True, mandatory=True), _c("cosmetic", True, pv=0, fv=0))) is True
    )


def test_errored_verdict_is_never_solved():
    """A crashed grade is not a solve. Conflating them is how lane faults have
    previously been recorded as results."""
    gold = _v(_c("m", True, mandatory=True))
    assert solved(_v(_c("m", True, mandatory=True), error="lane-fault: boom"), gold) is False


# --- authoring validation ----------------------------------------------------


def test_validate_flags_missing_mandatory_set():
    rep = validate_scoring("task-0001", _v(_c("a", True)))
    assert not rep.ok
    assert any("no mandatory" in e for e in rep.errors)


def test_validate_flags_gold_that_misses_a_check():
    gold = _v(_c("a", True, mandatory=True), _c("b", False))
    rep = validate_scoring("task-0001", gold)
    assert any("gold fails" in e for e in rep.errors)


def test_repeated_name_is_scored_once_not_summed():
    """44 of 50 tasks repeat a check name (conduct checks run per recreate).
    Summing them would apply a multiplier nobody authored."""
    gold = _v(_c("a", True, pv=1), _c("a", True, pv=1), _c("b", True, pv=1))
    assert max_score(gold) == 2  # not 3


def test_repeated_name_takes_the_worst_outcome():
    """If a property failed at any point in the run, it did not hold."""
    gold = _v(_c("a", True, pv=1, mandatory=True))
    run = _v(_c("a", True, pv=1, mandatory=True), _c("a", False, pv=1, mandatory=True))
    assert raw_score(run) == 0
    assert solved(run) is False
    assert task_score(run, gold) == 0.0


def test_repeated_preserve_check_penalises_once():
    """The multiplier bites hardest on preserve-style checks: un-deduped, one
    conduct regression would cost -1 per recreate."""
    run = _v(*[_c("conduct", False, pv=0, fv=-1) for _ in range(3)])
    assert raw_score(run) == -1


def test_validate_warns_on_repeats_but_errors_on_conflicting_scoring():
    # Identical scoring on both instances — the ordinary conduct-per-recreate case.
    gold = _v(
        _c("dup", True, mandatory=True),
        _c("dup", True, mandatory=True),
        _c("other", True),
    )
    rep = validate_scoring("task-0001", gold)
    assert rep.ok, rep.errors  # a plain repeat is legitimate
    assert any("more than once" in w for w in rep.warnings)

    conflicting = _v(_c("dup", True, pv=2, mandatory=True), _c("dup", True, pv=1))
    rep2 = validate_scoring("task-0001", conflicting)
    assert not rep2.ok
    assert any("DIFFERENT scoring" in e for e in rep2.errors)


def test_validate_flags_all_inert():
    gold = _v(_c("a", True, pv=0, fv=0, mandatory=True))
    rep = validate_scoring("task-0001", gold)
    assert any("inert" in e for e in rep.errors)


def test_validate_warns_on_legacy_calls():
    gold = _v(_c("a", True, mandatory=True))
    rep = validate_scoring("task-0001", gold, legacy_calls=7)
    assert rep.ok
    assert any("legacy" in w for w in rep.warnings)


def test_validate_passes_a_well_formed_task():
    gold = _v(_c("trap", True, pv=2, mandatory=True), _c("preserve", True, pv=0, fv=-1))
    rep = validate_scoring("task-0001", gold)
    assert rep.ok, rep.errors
    assert rep.max_score == 2


# --- legacy shims ------------------------------------------------------------


def test_legacy_bucket_defaults_match_the_authoring_guide():
    r = CheckRecorder()
    r.check_l1("a", True)
    r.check_hard("b", True)
    r.check_soft("c", True)
    r.check_l3("d", True)
    assert (r.l1[0].pass_value, r.l1[0].fail_value) == (1, 0)
    assert (r.hard[0].pass_value, r.hard[0].fail_value) == (0, -1)  # preserve-style
    assert (r.soft[0].pass_value, r.soft[0].fail_value) == (0, 0)  # cosmetic
    assert (r.l3[0].pass_value, r.l3[0].fail_value) == (1, 0)


def test_legacy_calls_are_counted_so_migration_is_measurable():
    r = CheckRecorder()
    r.check_l1("a", True)
    r.check("b", True, pass_value=2, mandatory=True)
    assert r.legacy_calls == ["a"]


def test_check_bucket_is_reporting_only():
    """bucket routes the check for display; it must not change the score."""
    r = CheckRecorder()
    r.check("a", True, pass_value=2, bucket="l3")
    assert r.l3[0].value == 2 and not r.l1


def test_unknown_bucket_is_loud():
    with pytest.raises(ValueError, match="unknown bucket"):
        CheckRecorder().check("a", True, bucket="nope")


# --- suite metrics -----------------------------------------------------------


def test_bench_score_is_the_mean_task_score():
    assert bench_score([100.0, 50.0, 0.0]) == pytest.approx(50.0)
    assert bench_score([]) == 0.0


def test_solve_rate_reports_a_wilson_interval():
    sr = solve_rate([True] * 24 + [False] * 26)
    assert sr.solved == 24 and sr.total == 50
    assert sr.rate == pytest.approx(0.48)
    # Matches the published sonnet figure: 24/50 = 0.480 [0.348, 0.615]
    assert sr.ci_low == pytest.approx(0.348, abs=0.002)
    assert sr.ci_high == pytest.approx(0.615, abs=0.002)


def test_ci_is_about_a_quarter_wide_at_n50():
    """The reason an ordering like 0.400 vs 0.375 is not a result."""
    assert 0.20 < solve_rate([True] * 20 + [False] * 30).ci_width < 0.30


def test_wilson_handles_the_degenerate_ends():
    assert wilson_interval(0, 0) == (0.0, 0.0)
    lo, hi = wilson_interval(0, 50)
    assert lo == 0.0 and 0.0 < hi < 0.1
    lo, hi = wilson_interval(50, 50)
    assert hi == 1.0 and 0.9 < lo < 1.0


def test_detail_may_be_positional_like_the_legacy_calls():
    """Regression: `detail` was keyword-only in the first cut, so every migrated
    scenario — which passes it positionally, exactly as check_l1 always allowed —
    raised TypeError and crashed the whole grade to zero checks. Caught only by
    a real Docker grade, not by unit tests."""
    r = CheckRecorder()
    r.check("a", True, "some detail", pass_value=2, mandatory=True)
    assert r.l1[0].detail == "some detail"
    assert r.l1[0].value == 2 and r.l1[0].mandatory is True


# --- the audit bar as code ----------------------------------------------------


def _bar_gold():
    return _v(_c("trap", True, pv=2, mandatory=True), _c("plumb", True, pv=0, fv=-1))


def test_probe_bar_passes_a_well_formed_task():
    from bench.scoring import check_probe_bar

    gold = _bar_gold()
    empty = _v(_c("trap", False, pv=2, mandatory=True), _c("plumb", True, pv=0, fv=-1))
    bar = check_probe_bar("t", gold, empty=empty)
    assert bar.ok, bar.failures
    assert (bar.gold_score, bar.empty_score) == (100.0, 0.0)


def test_probe_bar_mandatory_gate_zeros_empty_plumbing_credit():
    """An unchanged starter cannot bank plumbing credit past a failed core gate."""
    from bench.scoring import check_probe_bar

    gold = _v(_c("trap", True, pv=2, mandatory=True), _c("plumb", True, pv=1))
    empty = _v(_c("trap", False, pv=2, mandatory=True), _c("plumb", True, pv=1))
    bar = check_probe_bar("t", gold, empty=empty)
    assert bar.ok, bar.failures
    assert bar.empty_score == 0.0


def test_probe_bar_catches_a_gold_that_misses():
    """A mandatory miss gates even the gold probe to zero and fails validation."""
    from bench.scoring import check_probe_bar

    gold = _v(_c("trap", False, pv=2, mandatory=True), _c("x", True, pv=1))
    bar = check_probe_bar("t", gold)
    assert bar.gold_score == 0.0
    assert not bar.ok
    assert any("gold fails" in f for f in bar.failures)
    assert any("not Solved" in f for f in bar.failures)


def test_probe_bar_accepts_a_mandatory_gated_naive():
    """A docs-faithful implementation earns zero if the core gate fails."""
    from bench.scoring import check_probe_bar

    # Wide plumbing base, narrow trap: naive keeps 8 of 10 and clears 75%.
    plumb = [_c(f"p{i}", True, pv=1) for i in range(8)]
    gold = _v(_c("trap", True, pv=2, mandatory=True), *plumb)
    naive = _v(_c("trap", False, pv=2, mandatory=True), *plumb)
    bar = check_probe_bar("t", gold, naive=naive)
    assert bar.ok, bar.failures
    assert bar.naive_score == 0.0


def test_probe_bar_catches_a_solvable_naive():
    from bench.scoring import check_probe_bar

    gold = _v(_c("trap", True, pv=2, mandatory=True), _c("a", True))
    naive = _v(_c("trap", True, pv=2, mandatory=True), _c("a", False))
    bar = check_probe_bar("t", gold, naive=naive)
    assert any("naive is Solved" in f for f in bar.failures)


def test_absent_probes_are_skipped_not_passed():
    """A probe that was never run must not silently satisfy the bar."""
    from bench.scoring import check_probe_bar

    bar = check_probe_bar("t", _bar_gold())
    assert bar.ok
    assert bar.empty_score is None and bar.naive_score is None

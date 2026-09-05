"""The two scoring implementations must agree, forever.

harness/bench/scoring.py and integration_bench_v1/_common.py duplicate the same
formulas because the local and hosted lanes are separate codebases sharing only
the task tree. Duplication is the deliberate choice (same pattern as grader.py),
but undetected drift would mean hosted and local disagree about the same
submission — which is precisely the class of bug that has cost the most time on
this project.

pi's package __init__ imports `datasets`, which is not in the harness venv, so
_common is loaded directly by path.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from bench.scoring import max_score, solved, task_score
from bench.verdict import Check, Verdict

# tests/ -> harness/ -> integration-bench/ -> workspace root, where
# integration-bench-pi sits as a SIBLING of the public repo.
_PI_COMMON = (
    Path(__file__).resolve().parents[3]
    / "integration-bench-pi"
    / "integration_bench_v1"
    / "_common.py"
)


def _load_pi():
    if not _PI_COMMON.is_file():
        pytest.skip(f"pi _common.py not present at {_PI_COMMON}")
    spec = importlib.util.spec_from_file_location("pi_common_parity", _PI_COMMON)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pi_common_parity"] = mod
    spec.loader.exec_module(mod)
    if not hasattr(mod, "task_score_v3"):
        pytest.skip("pi _common.py predates scoring v3")
    return mod


# (name, ok, pass_value, fail_value, mandatory)
_GOLD = [("trap", True, 2, 0, True), ("plumb", True, 1, 0, False), ("pres", True, 0, -1, False)]
_CASES = {
    "gold": _GOLD,
    "partial": [
        ("trap", True, 2, 0, True),
        ("plumb", False, 1, 0, False),
        ("pres", True, 0, -1, False),
    ],
    "empty": [
        ("trap", False, 2, 0, True),
        ("plumb", False, 1, 0, False),
        ("pres", False, 0, -1, False),
    ],
    "regressed": [
        ("trap", True, 2, 0, True),
        ("plumb", True, 1, 0, False),
        ("pres", False, 0, -1, False),
    ],
    "repeat": [("dup", True, 1, 0, True), ("dup", True, 1, 0, True), ("x", True, 1, 0, False)],
}


def _h(spec, error=None):
    return Verdict(
        task="t",
        run_id="r",
        l1=[Check(name=n, ok=o, pass_value=p, fail_value=f, mandatory=m) for n, o, p, f, m in spec],
        error=error,
    )


def _p(spec, error=None):
    return {
        "l1": [
            {"name": n, "ok": o, "pass_value": p, "fail_value": f, "mandatory": m}
            for n, o, p, f, m in spec
        ],
        "l2": {"hard": [], "soft": {"results": []}},
        "l3": [],
        "error": error,
    }


@pytest.mark.parametrize("label", sorted(_CASES))
def test_lanes_agree_on_task_score_and_solved(label):
    pi = _load_pi()
    spec = _CASES[label]
    gh, gp = _h(_GOLD), _p(_GOLD)
    vh, vp = _h(spec), _p(spec)
    assert round(task_score(vh, gh), 9) == round(pi.task_score_v3(vp, gp), 9), label
    assert solved(vh, gh) == pi.solved_v3(vp, gp), label


def test_lanes_agree_on_max_score():
    pi = _load_pi()
    assert max_score(_h(_GOLD)) == pi.max_score_v3(_p(_GOLD))


def test_lanes_agree_that_a_broken_ceiling_is_not_zero():
    """harness raises, pi returns None — different idioms, same refusal to
    return a score. What must never happen is either returning 0.0."""
    from bench.scoring import ScoringError

    pi = _load_pi()
    spec = [("pres", True, 0, -1, False)]
    with pytest.raises(ScoringError):
        task_score(_h(spec), _h(spec))
    assert pi.task_score_v3(_p(spec), _p(spec)) is None


def test_lanes_agree_an_errored_verdict_is_unsolved():
    pi = _load_pi()
    spec = [("m", True, 1, 0, True)]
    assert solved(_h(spec, error="lane-fault: x"), _h(spec)) is False
    assert pi.solved_v3(_p(spec, error="lane-fault: x"), _p(spec)) is False


def test_lanes_agree_a_verdict_with_no_mandatory_check_recorded_is_unsolved():
    """The early-return path. A scenario that bails when its output is unreadable
    records only the plumbing checks, so the do-nothing probe's verdict carries no
    mandatory check at all. `all()` over the empty set would report Solved at a
    score of 0 — measured on task-0006, where all three scenarios bail this way."""
    pi = _load_pi()
    spec = [("push_exit_ok", False, 1, 0, False), ("result_readable", False, 0, -1, False)]
    assert solved(_h(spec), _h(spec)) is False
    assert pi.solved_v3(_p(spec), _p(spec)) is False


def test_lanes_agree_missing_gold_mandatory_check_is_unsolved_and_scored_failed():
    pi = _load_pi()
    gold = [
        ("reached", True, 1, 0, True),
        ("omitted", True, 2, -1, True),
    ]
    run = [("reached", True, 1, 0, True)]
    gh, gp = _h(gold), _p(gold)
    vh, vp = _h(run), _p(run)
    assert solved(vh, gh) is False
    assert pi.solved_v3(vp, gp) is False
    assert task_score(vh, gh) == 0.0
    assert pi.task_score_v3(vp, gp) == 0.0

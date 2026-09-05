"""Schema-2 sidecar: the per-check three-probe table (SCORING-V2.md §4.1/§6.3)
and the partition-aware strict gate.

Gold defines the rows; empty/stub instances align by (bucket, name,
occurrence); a probe that never recorded an instance contributes False —
unreached is failed, the same rule submissions are scored by.
"""

from bench.commands.validate import (
    build_baseline_sidecar,
    build_checks_v2,
    fixed_check_fraction,
    strict_gate_passes,
)


def _verdict(l1=(), hard=(), l3=(), soft=()):
    def checks(spec):
        return [{"name": n, "ok": ok} for n, ok in spec]

    return {
        "l1": checks(l1),
        "l2": {"hard": checks(hard), "soft": {"results": checks(soft)}},
        "l3": checks(l3),
    }


def test_rows_come_from_gold_and_carry_all_three_probes():
    gold = _verdict(l1=[("out_rows", True)], hard=[("no_leak", True)], soft=[("polite", True)])
    empty = _verdict(l1=[("out_rows", False)], hard=[("no_leak", True)], soft=[("polite", True)])
    stub = _verdict(l1=[("out_rows", False)], hard=[("no_leak", True)], soft=[("polite", False)])
    rows = build_checks_v2(gold, empty, stub)
    probes = [{k: r[k] for k in ("bucket", "name", "gold", "empty", "stub")} for r in rows]
    assert probes == [
        {"bucket": "l1", "name": "out_rows", "gold": True, "empty": False, "stub": False},
        {"bucket": "hard", "name": "no_leak", "gold": True, "empty": True, "stub": True},
        {"bucket": "soft", "name": "polite", "gold": True, "empty": True, "stub": False},
    ]
    # Rows also carry the authored scoring (added 2026-08-07) so a migrated task
    # can be seen as migrated; defaults here because _verdict() builds plain checks.
    for r in rows:
        assert set(r) >= {"pass_value", "fail_value", "mandatory"}


def test_rows_carry_the_authored_scoring_from_gold():
    """Without this the sidecar loses pass_value/mandatory and
    `bench scoring-status` can never show a migrated task as done."""
    gold = {
        "l1": [{"name": "trap", "ok": True, "pass_value": 2, "fail_value": 0, "mandatory": True}],
        "l2": {"hard": [], "soft": {"results": []}},
        "l3": [],
    }
    (row,) = build_checks_v2(gold, None, None)
    assert (row["pass_value"], row["fail_value"], row["mandatory"]) == (2, 0, True)


def test_positive_check_allowlist_zeros_plumbing_but_preserves_penalties():
    gold = {
        "l1": [
            {"name": "core", "ok": True, "pass_value": 3, "fail_value": 0},
            {"name": "already_worked", "ok": True, "pass_value": 1, "fail_value": 0},
        ],
        "l2": {
            "hard": [{"name": "no_secret_leak", "ok": True, "pass_value": 0, "fail_value": -1}],
            "soft": {"results": []},
        },
        "l3": [],
    }
    rows = build_checks_v2(
        gold,
        None,
        None,
        positive_check_allowlist={"core"},
        mandatory_check_allowlist={"core", "no_secret_leak"},
    )
    scoring = {
        row["name"]: (row["pass_value"], row["fail_value"], row["mandatory"]) for row in rows
    }
    assert scoring == {
        "core": (1, 0, True),
        "already_worked": (0, 0, False),
        "no_secret_leak": (0, -1, True),
    }


def test_probe_instance_missing_counts_as_failed():
    """The empty probe aborted before scenario 2: its second `app_exit_ok`
    instance was never recorded -> False, not skipped."""
    gold = _verdict(l1=[("app_exit_ok", True), ("app_exit_ok", True)])
    empty = _verdict(l1=[("app_exit_ok", True)])
    rows = build_checks_v2(gold, empty, None)
    assert [r["empty"] for r in rows] == [True, False]


def test_fixed_check_fraction_ignores_candidate_only_checks_and_fails_missing():
    gold = _verdict(l1=[("a", True), ("b", True)])
    candidate = _verdict(l1=[("a", True), ("extra", True)])
    assert fixed_check_fraction(gold, candidate) == 0.5


def test_duplicate_names_align_by_occurrence_not_by_name():
    gold = _verdict(l1=[("dup", True), ("dup", True)])
    empty = _verdict(l1=[("dup", False), ("dup", True)])
    rows = build_checks_v2(gold, empty, None)
    assert [r["empty"] for r in rows] == [False, True]


def test_skipped_stub_probe_yields_none_for_every_row():
    gold = _verdict(l1=[("a", True)], l3=[("b", True)])
    rows = build_checks_v2(gold, _verdict(), None)
    assert all(r["stub"] is None for r in rows)


def test_gold_miss_rows_are_recorded_not_dropped():
    gold = _verdict(l1=[("a", True)], soft=[("gold_misses_this", False)])
    rows = build_checks_v2(gold, _verdict(), _verdict())
    assert {r["name"]: r["gold"] for r in rows} == {"a": True, "gold_misses_this": False}


def test_sidecar_schema2_is_additive_and_carries_the_table():
    gold = {
        "l1": [{"name": "a", "ok": True, "pass_value": 1, "fail_value": 0, "mandatory": True}],
        "l2": {"hard": [], "soft": {"results": []}},
        "l3": [],
    }
    rows = build_checks_v2(gold, _verdict(), None)
    payload = build_baseline_sidecar(
        n_total=1,
        empty_fraction=0.0,
        stub_fraction=None,
        gold_fraction=1.0,
        checks={"l1": ["a"], "hard": [], "l3": [], "soft": []},
        checks_v2=rows,
    )
    assert payload["schema"] == 2
    # every schema-1 field retained
    for key in ("n_total", "floor_fraction", "gold_fraction", "probes", "checks", "generated"):
        assert key in payload
    assert payload["checks_v2"] == rows
    assert payload["completion"] == {
        "mode": "authored_mandatory_checks",
        "required_checks": ["a"],
    }
    assert payload["scoring"] == {
        "version": "task-score-v4-mandatory-gated",
        "empty_raw": 0,
        "empty_task_score": 0.0,
        "stub_raw": None,
        "stub_task_score": None,
        "floor_task_score": 0.0,
    }


def test_sidecar_without_probe_verdicts_omits_the_table():
    payload = build_baseline_sidecar(
        n_total=1,
        empty_fraction=0.0,
        stub_fraction=None,
        gold_fraction=1.0,
        checks={"l1": ["a"], "hard": [], "l3": [], "soft": []},
    )
    assert "checks_v2" not in payload


def test_sidecar_fixed_gold_floor_uses_worst_duplicate_outcome():
    rows = [
        {
            "bucket": "l1",
            "name": "required",
            "gold": True,
            "empty": True,
            "stub": True,
            "pass_value": 2,
            "fail_value": 0,
            "mandatory": True,
        },
        {
            "bucket": "l1",
            "name": "required",
            "gold": True,
            "empty": False,
            "stub": True,
            "pass_value": 2,
            "fail_value": 0,
            "mandatory": True,
        },
        {
            "bucket": "hard",
            "name": "conduct",
            "gold": True,
            "empty": False,
            "stub": False,
            "pass_value": 0,
            "fail_value": -1,
            "mandatory": False,
        },
    ]
    payload = build_baseline_sidecar(
        n_total=3,
        empty_fraction=2 / 3,  # legacy recorded-instance fraction is intentionally different
        stub_fraction=1.0,
        gold_fraction=1.0,
        checks={"l1": ["required", "required"], "hard": ["conduct"], "l3": [], "soft": []},
        checks_v2=rows,
    )
    assert payload["floor_fraction"] == 1.0
    assert payload["scoring"] == {
        "version": "task-score-v4-mandatory-gated",
        "empty_raw": -1,
        "empty_task_score": 0.0,
        "stub_raw": 1,
        "stub_task_score": 50.0,
        "floor_task_score": 50.0,
    }


# --- partition-aware strict gate -------------------------------------------------


def _rows(n_disc, n_pres=0, n_vac=0):
    rows = [
        {"bucket": "l1", "name": f"d{i}", "gold": True, "empty": False, "stub": False}
        for i in range(n_disc)
    ]
    rows += [
        {"bucket": "l1", "name": f"p{i}", "gold": True, "empty": True, "stub": False}
        for i in range(n_pres)
    ]
    rows += [
        {"bucket": "l1", "name": f"v{i}", "gold": True, "empty": True, "stub": True}
        for i in range(n_vac)
    ]
    return rows


def test_strict_gate_uses_measured_discriminating_count_for_fix_tasks():
    """Headroom estimate says 9 (0.3*32) — under the old bar this fails the
    >=8 check only if the estimate is wrong. The measured partition (12
    discriminating) is authoritative."""
    ok, rationale = strict_gate_passes(
        floor_fraction=0.7,
        gold_fraction=1.0,
        n_total=32,
        category="fix",
        checks_v2=_rows(n_disc=12, n_pres=18, n_vac=2),
    )
    assert ok
    assert "12 discriminating" in rationale
    assert "measured partition" in rationale


def test_strict_gate_hard_fails_zero_discriminating_for_every_category():
    for category in ("build", "fix", "harden", "migrate", ""):
        ok, rationale = strict_gate_passes(
            floor_fraction=0.0,
            gold_fraction=1.0,
            n_total=10,
            category=category,
            checks_v2=_rows(n_disc=0, n_pres=8, n_vac=2),
        )
        assert not ok
        assert "unwinnable" in rationale


def test_strict_gate_without_table_keeps_v1_behavior():
    ok, _ = strict_gate_passes(floor_fraction=0.2, gold_fraction=1.0, n_total=20, category="build")
    assert ok
    ok, _ = strict_gate_passes(floor_fraction=0.6, gold_fraction=1.0, n_total=20, category="build")
    assert not ok

"""Regression tests for the four blind spots found in the 2026-08-06 audit of
`bench validate-suite`.

Each of these rules exists because the lint reported a clean bill of health on
a tree that was demonstrably not clean:

  1. f-string check names collapsed to a contentless literal (`outcome_*_is_`),
     so per-row check families both lost their content and falsely unified.
  2. every generated check name carried its own task id as a prefix, which made
     every task's check_name_set disjoint by construction and pinned check-name
     Jaccard at 0.000 for byte-identical tasks.
  3. no rule looked at what a check actually GRADES ON, so 184 tasks whose
     entire grading surface was `output_file == golden_fixture` passed.
  4. every rule compared tasks within one suite, so a held-out task re-running
     a public task's mechanic on the same vendor was invisible.

The synthetic cases are hermetic. The two tests that read the real held-out
scaffolds skip when that tree is not checked out beside this repo.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from bench.scoring import gold_from_sidecar

from bench.commands.validate_suite import (
    _joined_str_template,
    _strip_leading_task_id,
    build_task_signature,
    jaccard,
    lint_suite,
    mechanic_family_tokens,
    same_mechanic_family,
)

PUBLIC_TASKS = Path(__file__).resolve().parents[2] / "tasks" / "public"


def test_every_public_task_has_a_valid_explicit_completion_manifest():
    tasks = sorted(PUBLIC_TASKS.glob("task-*"))
    assert len(tasks) == 50
    for task in tasks:
        gold = gold_from_sidecar(task)
        assert any(c.mandatory for c in (*gold.l1, *gold.hard, *gold.soft, *gold.l3)), task.name
        sidecar = json.loads((task / "verifier" / "empty-baseline.json").read_text())
        scoring = sidecar.get("scoring") or {}
        assert scoring.get("version") in {
            "task-score-v3-fixed-gold",
            "task-score-v4-mandatory-gated",
        }, task.name
        assert isinstance(scoring.get("empty_raw"), int), task.name
        assert isinstance(scoring.get("empty_task_score"), (int, float)), task.name
        assert isinstance(scoring.get("floor_task_score"), (int, float)), task.name


def test_public_gold_patches_do_not_embed_generated_runtime_artifacts():
    tasks = PUBLIC_TASKS
    forbidden = re.compile(
        r"^diff --git a/(?:\.pytest_cache/|\.venv/|venv/|__pycache__/|.*\.egg-info/)",
        re.MULTILINE,
    )
    bad = [
        patch.parent.name
        for patch in sorted(tasks.glob("task-*/authoring/solution.patch"))
        if forbidden.search(patch.read_text(encoding="utf-8", errors="replace"))
    ]
    assert bad == []


_HOLDOUT_REPO = os.environ.get("IB_HOLDOUT_REPO", "").strip()
HOLDOUT_TASKS = (
    Path(_HOLDOUT_REPO).expanduser() / "tasks"
    if _HOLDOUT_REPO
    else Path("/__integration_bench_holdout_not_configured__/tasks")
)

requires_holdout = pytest.mark.skipif(
    not HOLDOUT_TASKS.is_dir(),
    reason=f"held-out suite not checked out at {HOLDOUT_TASKS}",
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _write_task(
    tasks_dir: Path,
    task_id: str,
    *,
    scenario_source: str,
    vendor: str = "acme",
    scenarios: list[str] | None = None,
    primary_mechanic: str | None = None,
    fixture_files: dict[str, Any] | None = None,
) -> Path:
    """A minimal task dir whose single scenario module is `scenario_source`."""
    task_dir = tasks_dir / task_id
    (task_dir / "verifier" / "scenarios").mkdir(parents=True, exist_ok=True)
    (task_dir / "verifier" / "fixtures").mkdir(parents=True, exist_ok=True)

    task_yaml: dict[str, Any] = {
        "id": task_id,
        "category": "build",
        "vendor": vendor,
        "scenarios": scenarios if scenarios is not None else ["main"],
        "entry": {"command": ["python", "-m", "x"]},
        "vendors": {vendor: {"image": f"{vendor}:local", "entities": {}}},
    }
    if primary_mechanic is not None:
        task_yaml["primary_mechanic"] = primary_mechanic
    (task_dir / "task.yaml").write_text(yaml.safe_dump(task_yaml), encoding="utf-8")
    (task_dir / "verifier" / "scenarios" / "main.py").write_text(scenario_source, encoding="utf-8")
    for filename, content in (fixture_files or {}).items():
        (task_dir / "verifier" / "fixtures" / filename).write_text(
            json.dumps(content), encoding="utf-8"
        )
    return task_dir


def _mechanics(tmp_path: Path, slugs: dict[str, str]) -> Path:
    path = tmp_path / "mechanics.yaml"
    path.write_text(yaml.safe_dump(slugs), encoding="utf-8")
    return path


# The generated scaffold's grading surface, verbatim in shape: run the app,
# then compare each declared output file to a golden fixture. Nothing else.
FIXTURE_ONLY_SCENARIO = """
from pathlib import Path
import json


def _compare_json(a: Path, b: Path):
    if not a.exists():
        return False, f"missing {a.name}"
    try:
        aa = json.loads(a.read_text())
        bb = json.loads(b.read_text())
    except Exception as exc:
        return False, f"json parse error: {exc}"
    return aa == bb, f"{a.name} json differs"


async def run(ctx) -> None:
    returncode, _, stderr = ctx.app.run()
    ctx.check_l1("{TID}_app_exit_zero", returncode == 0, f"exit={returncode}")
    ok, detail = _compare_json(ctx.output_dir / "result.json", ctx.fixtures / "result.json")
    ctx.check_l1("{TID}_result.json_matches_fixture", ok, detail)
    ok, detail = _compare_json(ctx.output_dir / "writeback_log.json", ctx.fixtures / "writeback_log.json")
    ctx.check_l1("{TID}_writeback_log.json_matches_fixture", ok, detail)
"""

# The same task, but one check grades on the vendor request log -- i.e. on how
# the connector BEHAVED, not just what it emitted.
REQUEST_LOG_SCENARIO = """
from pathlib import Path
import json


def _compare_json(a: Path, b: Path):
    if not a.exists():
        return False, f"missing {a.name}"
    return json.loads(a.read_text()) == json.loads(b.read_text()), "differs"


async def run(ctx) -> None:
    returncode, _, stderr = ctx.app.run()
    ctx.check_l1("app_exit_zero", returncode == 0, f"exit={returncode}")
    ok, detail = _compare_json(ctx.output_dir / "result.json", ctx.fixtures / "result.json")
    ctx.check_l1("result_matches_fixture", ok, detail)
    log = ctx.vendor("acme").request_log()
    writes = [e for e in log if e.get("method") == "PATCH"]
    ctx.check_l1("no_redundant_writes", len(writes) == 3, f"{len(writes)} writes")
"""


# ---------------------------------------------------------------------------
# 1. f-string check names resolve to a stable template
# ---------------------------------------------------------------------------


def test_joined_str_template_keeps_literals_and_marks_slots():
    import ast

    node = ast.parse("f\"outcome_{row['ref']}_is_{row['outcome']}\"", mode="eval").body
    assert _joined_str_template(node) == "outcome_{}_is_{}"


def test_fstring_check_name_is_not_the_old_collapsed_literal(tmp_path):
    """The pre-fix renderer produced `outcome_*_is_` -- literals joined by `*`,
    interpolations dropped. That token names nothing."""
    tasks_dir = tmp_path / "tasks"
    source = (
        "async def run(ctx):\n"
        "    for row in ctx.rows:\n"
        "        ctx.check_l1(f\"outcome_{row['ref']}_is_{row['outcome']}\", True, \"\")\n"
    )
    _write_task(tasks_dir, "task-0001", scenario_source=source)

    report = lint_suite(tasks_dir, mechanics_path=_mechanics(tmp_path, {}))
    names = report.signatures["task-0001"].check_name_set

    assert "outcome_{}_is_{}" in names
    assert "outcome_*_is_" not in names


def test_fstring_templates_of_different_arity_do_not_unify(tmp_path):
    """Two unrelated per-row families that share their constant fragments used
    to collapse onto the SAME element, manufacturing false overlap. Preserving
    slot count and position keeps them distinct."""
    tasks_dir = tmp_path / "tasks"
    _write_task(
        tasks_dir,
        "task-0001",
        vendor="acme",
        scenario_source=(
            "async def run(ctx):\n"
            "    for row in ctx.rows:\n"
            "        ctx.check_l1(f\"outcome_{row['ref']}_is_{row['outcome']}\", True, \"\")\n"
        ),
    )
    _write_task(
        tasks_dir,
        "task-0002",
        vendor="globex",
        scenario_source=(
            "async def run(ctx):\n"
            "    for row in ctx.rows:\n"
            '        ctx.check_l1(f"outcome_{row[\'ref\']}_is_", True, "")\n'
        ),
    )

    report = lint_suite(tasks_dir, mechanics_path=_mechanics(tmp_path, {}))
    a = report.signatures["task-0001"].check_name_set
    b = report.signatures["task-0002"].check_name_set

    assert a == {"outcome_{}_is_{}"}
    assert b == {"outcome_{}_is_"}
    assert jaccard(a, b) == 0.0


# ---------------------------------------------------------------------------
# 2. leading task-id prefixes are stripped before similarity
# ---------------------------------------------------------------------------


def test_strip_leading_task_id_forms():
    assert _strip_leading_task_id("task-0058_app_exit_zero") == "app_exit_zero"
    assert _strip_leading_task_id("task_0058_app_exit_zero") == "app_exit_zero"
    assert _strip_leading_task_id("task0058-app_exit_zero") == "app_exit_zero"


def test_strip_leading_task_id_does_not_eat_real_names():
    """Guard against over-stripping: only a leading `task<digits>` SEPARATOR
    prefix goes. Anything else is task content and must survive intact."""
    assert (
        _strip_leading_task_id("task_completed_before_deadline") == "task_completed_before_deadline"
    )
    assert _strip_leading_task_id("tasks_reconciled") == "tasks_reconciled"
    assert _strip_leading_task_id("app_exit_zero") == "app_exit_zero"
    # A task id in the MIDDLE of a name is not identity noise at the front.
    assert _strip_leading_task_id("emitted_task-0058_row") == "emitted_task-0058_row"


def test_task_id_prefixed_identical_tasks_now_fail_jaccard(tmp_path):
    """Two tasks with byte-identical check structure, each prefixing its own
    task id. Pre-fix: Jaccard 0.000 and no finding. Post-fix: 1.000, FAIL."""
    tasks_dir = tmp_path / "tasks"
    for task_id, vendor in (("task-0058", "acme"), ("task-0064", "globex")):
        source = (
            "async def run(ctx):\n"
            f'    ctx.check_l1("{task_id}_app_exit_zero", True, "")\n'
            f'    ctx.check_l1("{task_id}_result.json_matches_fixture", True, "")\n'
            f'    ctx.check_l1("{task_id}_writeback_log.json_matches_fixture", True, "")\n'
        )
        _write_task(tasks_dir, task_id, vendor=vendor, scenario_source=source)

    report = lint_suite(tasks_dir, mechanics_path=_mechanics(tmp_path, {}))

    pair = {frozenset((a, b)): s for a, b, s in report.jaccard_pairs}
    assert pair[frozenset(("task-0058", "task-0064"))] == 1.0
    high = [f for f in report.findings if f.rule == "check_name_jaccard_high"]
    assert len(high) == 1
    assert high[0].level == "FAIL"
    assert high[0].tasks == ["task-0058", "task-0064"]


def test_prefix_strip_does_not_manufacture_overlap(tmp_path):
    """Stripping identity must not make genuinely different tasks look alike."""
    tasks_dir = tmp_path / "tasks"
    _write_task(
        tasks_dir,
        "task-0058",
        vendor="acme",
        scenario_source=(
            "async def run(ctx):\n"
            '    ctx.check_l1("task-0058_pagination_converged", True, "")\n'
            '    ctx.check_l1("task-0058_no_duplicate_rows", True, "")\n'
        ),
    )
    _write_task(
        tasks_dir,
        "task-0064",
        vendor="globex",
        scenario_source=(
            "async def run(ctx):\n"
            '    ctx.check_l1("task-0064_retry_after_respected", True, "")\n'
            '    ctx.check_l1("task-0064_token_refreshed_once", True, "")\n'
        ),
    )

    report = lint_suite(tasks_dir, mechanics_path=_mechanics(tmp_path, {}))

    pair = {frozenset((a, b)): s for a, b, s in report.jaccard_pairs}
    assert pair[frozenset(("task-0058", "task-0064"))] == 0.0
    assert not any(f.rule.startswith("check_name_jaccard") for f in report.findings)


@requires_holdout
def test_real_holdout_task_ids_are_stripped_before_jaccard():
    """Identity prefixes must not survive into the compared sets.

    Previously this also asserted the pair scored exactly 0.750 and cleared the
    0.55 FAIL bar. Both tasks have since been reworked, so the score legitimately
    moved and the assertion was pinning a snapshot of task content rather than the
    behaviour under test. Rewritten 2026-08-08.

    Prefix stripping is the actual fix this test exists for — it is what turned two
    byte-identical scaffolds from a misleading 0.000 into a truthful 0.750 — and it
    is a property of the code, so it stays. The numeric score belongs in a
    synthetic test that owns its inputs; see
    test_task_id_prefixed_identical_tasks_now_fail_jaccard above."""
    a = build_task_signature(HOLDOUT_TASKS / "task-0058")
    b = build_task_signature(HOLDOUT_TASKS / "task-0064")

    assert not a.errors and not b.errors
    assert a.check_name_set and b.check_name_set, "no check names extracted"
    assert all(not n.startswith("task-0058") for n in a.check_name_set)
    assert all(not n.startswith("task-0064") for n in b.check_name_set)
    # Whatever the content, the score has to be a well-formed similarity.
    score = jaccard(a.check_name_set, b.check_name_set)
    assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# 3. vacuous fixture-only grading
# ---------------------------------------------------------------------------


def test_fixture_only_task_fails_vacuity(tmp_path):
    tasks_dir = tmp_path / "tasks"
    _write_task(
        tasks_dir,
        "task-0001",
        scenario_source=FIXTURE_ONLY_SCENARIO.replace("{TID}", "task-0001"),
    )

    report = lint_suite(tasks_dir, mechanics_path=_mechanics(tmp_path, {}))

    vac = [f for f in report.findings if f.rule == "vacuous_fixture_only_grading"]
    assert len(vac) == 1
    assert vac[0].level == "FAIL"
    assert vac[0].tasks == ["task-0001"]

    profile = report.signatures["task-0001"].evidence
    assert profile.fixture_blob == 2
    assert profile.trivial == 1
    assert profile.substantive == 0


def test_request_log_evidence_clears_vacuity(tmp_path):
    """One check that consults the vendor request log is enough: the task is
    grading conduct, not just bytes."""
    tasks_dir = tmp_path / "tasks"
    _write_task(tasks_dir, "task-0001", scenario_source=REQUEST_LOG_SCENARIO)

    report = lint_suite(tasks_dir, mechanics_path=_mechanics(tmp_path, {}))

    assert not any(f.rule == "vacuous_fixture_only_grading" for f in report.findings)
    assert report.signatures["task-0001"].evidence.substantive >= 1


@pytest.mark.parametrize(
    "evidence_call",
    ["token_log()", "webhook_deliveries()", "state('candidate')"],
)
def test_other_vendor_evidence_surfaces_also_clear_vacuity(tmp_path, evidence_call):
    tasks_dir = tmp_path / "tasks"
    source = (
        "import json\n"
        "async def run(ctx):\n"
        '    ctx.check_l1("result_matches", json.loads((ctx.output_dir / "r.json").read_text())'
        ' == json.loads((ctx.fixtures / "r.json").read_text()), "")\n'
        f"    seen = ctx.vendor('acme').{evidence_call}\n"
        '    ctx.check_l1("delivery_evidence", len(seen) == 1, "")\n'
    )
    _write_task(tasks_dir, "task-0001", scenario_source=source)

    report = lint_suite(tasks_dir, mechanics_path=_mechanics(tmp_path, {}))

    assert not any(f.rule == "vacuous_fixture_only_grading" for f in report.findings)


def test_unresolvable_evidence_suppresses_vacuity(tmp_path):
    """The tracer is deliberately biased to false negatives: a check it cannot
    account for counts as substantive, so the rule stays quiet rather than
    accusing a task it does not understand."""
    tasks_dir = tmp_path / "tasks"
    source = (
        "import json\n"
        "async def run(ctx):\n"
        '    ctx.check_l1("result_matches", json.loads((ctx.output_dir / "r.json").read_text())'
        ' == json.loads((ctx.fixtures / "r.json").read_text()), "")\n'
        "    verdict = some_helper_from_who_knows_where()\n"
        '    ctx.check_l1("mystery", verdict, "")\n'
    )
    _write_task(tasks_dir, "task-0001", scenario_source=source)

    report = lint_suite(tasks_dir, mechanics_path=_mechanics(tmp_path, {}))

    assert not any(f.rule == "vacuous_fixture_only_grading" for f in report.findings)


def test_task_with_no_checks_is_not_reported_vacuous(tmp_path):
    """No fixture comparison at all -> a different problem, not this rule's."""
    tasks_dir = tmp_path / "tasks"
    _write_task(tasks_dir, "task-0001", scenario_source="async def run(ctx):\n    pass\n")

    report = lint_suite(tasks_dir, mechanics_path=_mechanics(tmp_path, {}))

    assert not any(f.rule == "vacuous_fixture_only_grading" for f in report.findings)


def test_exit_code_only_task_is_not_reported_vacuous(tmp_path):
    tasks_dir = tmp_path / "tasks"
    source = (
        "async def run(ctx):\n"
        "    returncode, _, stderr = ctx.app.run()\n"
        '    ctx.check_l1("app_exit_zero", returncode == 0, "")\n'
    )
    _write_task(tasks_dir, "task-0001", scenario_source=source)

    report = lint_suite(tasks_dir, mechanics_path=_mechanics(tmp_path, {}))

    assert not any(f.rule == "vacuous_fixture_only_grading" for f in report.findings)


@requires_holdout
def test_evidence_profile_is_computable_on_real_holdout_tasks():
    """The rule runs against real task trees and classifies every check.

    This USED to assert that task-0058/0064 were vacuous, with exact
    fixture_blob counts. Those tasks have since been reworked — 0058 now profiles
    substantive=9 — so the assertion encoded a snapshot of mutable task content,
    not a property of the rule, and it failed for the RIGHT reason: the tasks got
    better. Rewritten 2026-08-08 to assert what is actually stable.

    The vacuity rule itself is covered by the synthetic tests above
    (test_fixture_only_task_fails_vacuity and friends), which own
    their input and cannot rot. What only a real tree can check is that the
    analysis survives real scenario source: no crash, and every check landing in
    exactly one of the three buckets."""
    for task_id in ("task-0058", "task-0064"):
        sig = build_task_signature(HOLDOUT_TASKS / task_id)
        assert not sig.errors, f"{task_id}: {sig.errors}"
        ev = sig.evidence
        total = ev.trivial + ev.fixture_blob + ev.substantive
        assert total > 0, f"{task_id} classified no checks at all"
        # is_vacuous must be a function of the profile, not an independent flag.
        assert ev.is_vacuous == (ev.substantive == 0 and ev.fixture_blob > 0)


@requires_holdout
def test_reworked_holdout_task_0053_is_not_vacuous():
    """The counter-example that keeps the rule honest: task-0053 was genuinely
    reworked and grades on vendor state, so it must NOT be flagged."""
    sig = build_task_signature(HOLDOUT_TASKS / "task-0053")
    assert not sig.evidence.is_vacuous
    assert sig.evidence.substantive > 0


# ---------------------------------------------------------------------------
# 4. cross-suite mechanic reuse
# ---------------------------------------------------------------------------


def test_mechanic_family_tokens_drops_generic_filler():
    assert mechanic_family_tokens("hmac_clock_skew") == {"hmac", "clock", "skew"}
    assert "sync" not in mechanic_family_tokens("incremental_sync_watermark")


def test_same_mechanic_family_matches_restatements():
    assert same_mechanic_family("hmac_clock_skew", "hmac_clock_skew")
    assert same_mechanic_family("hmac_clock_skew", "container_clock_hmac_failure")
    assert same_mechanic_family("conflict_refetch_retry", "conflict_refetch_backoff")


def test_same_mechanic_family_rejects_single_shared_noun():
    # The pair audit pass 3 deliberately redesigned apart -- must stay quiet.
    assert not same_mechanic_family("conflict_refetch_retry", "conflict_body_reuse_efficiency")
    assert not same_mechanic_family("tombstone_timestamp_skew", "tombstone_skip_on_import")
    assert not same_mechanic_family("ooo_replay_convergence", "dlq_replay_dedupe_tool")


def test_cross_suite_same_vendor_same_family_warns(tmp_path):
    public = tmp_path / "public"
    holdout = tmp_path / "holdout"
    _write_task(
        public,
        "task-0001",
        vendor="staffline",
        scenario_source="async def run(ctx):\n    pass\n",
        primary_mechanic="hmac_clock_skew",
    )
    _write_task(
        holdout,
        "task-0191",
        vendor="staffline",
        scenario_source="async def run(ctx):\n    pass\n",
        primary_mechanic="container_clock_hmac_failure",
    )

    report = lint_suite(
        public,
        mechanics_path=_mechanics(tmp_path, {"hmac_clock_skew": "x"}),
        compare_tasks_dir=holdout,
        compare_label="holdout",
    )

    cross = [f for f in report.findings if f.rule == "cross_suite_mechanic_reuse"]
    assert len(cross) == 1
    assert cross[0].level == "WARN"
    assert cross[0].tasks == ["task-0001", "holdout:task-0191"]


def test_cross_suite_different_vendor_does_not_warn(tmp_path):
    """Same mechanic on a DIFFERENT vendor is a legitimate second exercise."""
    public = tmp_path / "public"
    holdout = tmp_path / "holdout"
    _write_task(
        public,
        "task-0001",
        vendor="staffline",
        scenario_source="async def run(ctx):\n    pass\n",
        primary_mechanic="hmac_clock_skew",
    )
    _write_task(
        holdout,
        "task-0191",
        vendor="globalhire",
        scenario_source="async def run(ctx):\n    pass\n",
        primary_mechanic="hmac_clock_skew",
    )

    report = lint_suite(
        public,
        mechanics_path=_mechanics(tmp_path, {"hmac_clock_skew": "x"}),
        compare_tasks_dir=holdout,
        compare_label="holdout",
    )

    assert not any(f.rule == "cross_suite_mechanic_reuse" for f in report.findings)


def test_cross_suite_unrelated_mechanic_same_vendor_does_not_warn(tmp_path):
    public = tmp_path / "public"
    holdout = tmp_path / "holdout"
    _write_task(
        public,
        "task-0030",
        vendor="bullpen",
        scenario_source="async def run(ctx):\n    pass\n",
        primary_mechanic="conflict_refetch_retry",
    )
    _write_task(
        holdout,
        "task-0172",
        vendor="bullpen",
        scenario_source="async def run(ctx):\n    pass\n",
        primary_mechanic="conflict_body_reuse_efficiency",
    )

    report = lint_suite(
        public,
        mechanics_path=_mechanics(tmp_path, {"conflict_refetch_retry": "x"}),
        compare_tasks_dir=holdout,
        compare_label="holdout",
    )

    assert not any(f.rule == "cross_suite_mechanic_reuse" for f in report.findings)


def test_cross_suite_rule_is_off_without_compare_dir(tmp_path):
    public = tmp_path / "public"
    _write_task(
        public,
        "task-0001",
        vendor="staffline",
        scenario_source="async def run(ctx):\n    pass\n",
        primary_mechanic="hmac_clock_skew",
    )

    report = lint_suite(public, mechanics_path=_mechanics(tmp_path, {"hmac_clock_skew": "x"}))

    assert not any(f.rule == "cross_suite_mechanic_reuse" for f in report.findings)


def test_cross_suite_flags_registered_in_cli():
    from bench.cli import _build_parser

    args = _build_parser().parse_args(
        ["validate-suite", "--compare-tasks-dir", "other/tasks", "--compare-label", "holdout"]
    )
    assert args.compare_tasks_dir == "other/tasks"
    assert args.compare_label == "holdout"


@requires_holdout
def test_real_cross_suite_sweep_finds_the_hmac_clock_pair():
    """End-to-end on the two real trees: exactly the one pair, and the
    already-redesigned near-misses stay quiet."""
    public = PUBLIC_TASKS
    report = lint_suite(public, compare_tasks_dir=HOLDOUT_TASKS, compare_label="holdout")

    cross = [f for f in report.findings if f.rule == "cross_suite_mechanic_reuse"]
    assert [f.tasks for f in cross] == [
        ["task-0001", "holdout:task-0191"],
        ["task-0021", "holdout:task-0178"],
    ]
    assert all(f.level == "WARN" for f in cross)

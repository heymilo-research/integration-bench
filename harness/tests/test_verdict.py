import json
from pathlib import Path

from bench.verdict import (
    Check,
    Verdict,
    verdict_semantic_diff,
    verdicts_equal_ignoring_run_id,
)

# contracts/verdict/v1.json lives at the repo root; this test file is at
# <repo>/harness/tests/test_verdict.py, so the repo root is parents[2].
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contracts" / "verdict" / "v1.json"


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def test_resolved_true_when_everything_ok():
    v = Verdict(
        task="task-0001",
        run_id="r1",
        l1=[Check("candidates_match", True)],
        hard=[Check("no_creds_in_query", True)],
        soft=[Check("retry_after_honored", True)],
        l3=[],
    )
    assert v.resolved is True


def test_resolved_false_on_l1_failure():
    v = Verdict(
        task="t",
        run_id="r1",
        l1=[Check("candidates_match", False, "missing cand_0900")],
        hard=[Check("no_creds_in_query", True)],
    )
    assert v.resolved is False


def test_resolved_false_on_hard_violation_even_if_l1_ok():
    v = Verdict(
        task="t",
        run_id="r1",
        l1=[Check("candidates_match", True)],
        hard=[Check("no_creds_in_query", False, "leaked in query")],
    )
    assert v.resolved is False


def test_resolved_false_on_declared_l3_failure():
    v = Verdict(
        task="t",
        run_id="r1",
        l1=[Check("x", True)],
        hard=[Check("y", True)],
        l3=[Check("resume_after_500", False, "restarted from page 1")],
    )
    assert v.resolved is False


def test_resolved_vacuously_true_with_no_l3_checks():
    v = Verdict(task="t", run_id="r1", l1=[Check("x", True)], hard=[Check("y", True)], l3=[])
    assert v.resolved is True


def test_error_forces_unresolved():
    v = Verdict.error_verdict("t", "r1", "patch did not apply")
    assert v.resolved is False
    assert v.to_dict()["error"] == "patch did not apply"


def test_soft_score_computation():
    v = Verdict(
        task="t",
        run_id="r1",
        soft=[Check("a", True), Check("b", False), Check("c", True), Check("d", False)],
    )
    d = v.to_dict()
    assert d["l2"]["soft"]["checks"] == 4
    assert d["l2"]["soft"]["violations"] == 2
    assert d["l2"]["soft"]["score"] == 0.5


def test_soft_score_is_one_when_no_soft_checks():
    v = Verdict(task="t", run_id="r1")
    assert v.to_dict()["l2"]["soft"]["score"] == 1.0


def test_to_dict_matches_required_schema_keys():
    v = Verdict(task="t", run_id="r1", l1=[Check("a", True)], hard=[Check("b", True)])
    d = v.to_dict()
    assert set(d.keys()) == {
        "schema_version",
        "task",
        "run_id",
        "resolved",
        "l1",
        "l2",
        "l3",
        "error",
    }
    assert set(d["l2"].keys()) == {"hard", "soft"}
    assert {"violations", "checks", "score"}.issubset(d["l2"]["soft"].keys())


def test_round_trip_to_dict_from_dict():
    v = Verdict(
        task="t",
        run_id="r1",
        l1=[Check("a", True, "ok")],
        hard=[Check("b", False, "bad")],
        soft=[Check("c", True, "")],
        l3=[Check("d", True, "")],
    )
    d = v.to_dict()
    v2 = Verdict.from_dict(d)
    assert v2.to_dict() == d


def test_verdicts_equal_ignoring_run_id():
    v1 = Verdict(task="t", run_id="run-A", l1=[Check("a", True)]).to_dict()
    v2 = Verdict(task="t", run_id="run-B", l1=[Check("a", True)]).to_dict()
    v3 = Verdict(task="t", run_id="run-C", l1=[Check("a", False)]).to_dict()
    assert verdicts_equal_ignoring_run_id(v1, v2) is True
    assert verdicts_equal_ignoring_run_id(v1, v3) is False


def test_verdicts_equal_ignores_diagnostic_details():
    # Details may embed wall-clock-dependent values (mint counts, windows);
    # equality is over names, ok flags, and scores — not diagnostics.
    v1 = Verdict(task="t", run_id="run-A", soft=[Check("reauth", True, "3 mint(s)")]).to_dict()
    v2 = Verdict(task="t", run_id="run-B", soft=[Check("reauth", True, "4 mint(s)")]).to_dict()
    v3 = Verdict(task="t", run_id="run-C", soft=[Check("reauth", False, "3 mint(s)")]).to_dict()
    assert verdicts_equal_ignoring_run_id(v1, v2) is True
    assert verdicts_equal_ignoring_run_id(v1, v3) is False


def test_verdict_semantic_diff_names_the_changed_check_path():
    v1 = Verdict(task="t", run_id="run-A", l1=[Check("a", True, "one")]).to_dict()
    v2 = Verdict(task="t", run_id="run-B", l1=[Check("a", False, "two")]).to_dict()
    differences = verdict_semantic_diff(v1, v2)
    assert "$.l1[0].ok: True != False" in differences
    assert not any("detail" in item or "run_id" in item for item in differences)


def test_schema_version_present_and_first():
    v = Verdict(task="t", run_id="r1")
    d = v.to_dict()
    assert d["schema_version"] == 1
    # schema_version must be the first key so consumers can branch before
    # interpreting anything else (dict insertion order is significant here).
    assert next(iter(d)) == "schema_version"


def test_error_verdict_also_carries_schema_version():
    d = Verdict.error_verdict("t", "r1", "patch did not apply").to_dict()
    assert d["schema_version"] == 1


def test_schema_file_declares_const_1():
    schema = _load_schema()
    assert schema["properties"]["schema_version"]["const"] == 1
    assert "schema_version" in schema["required"]


def _sample_verdict_dict() -> dict:
    """A verdict exercising every branch: l1/hard/soft/l3 populated, no error."""
    return Verdict(
        task="task-0001",
        run_id="r1",
        l1=[Check("candidates_match", True, "ok")],
        hard=[Check("no_creds_in_query", True)],
        soft=[Check("retry_after_honored", True), Check("page_size", False, "too big")],
        l3=[Check("resume_after_500", True)],
    ).to_dict()


def test_verdict_validates_against_schema():
    schema = _load_schema()
    d = _sample_verdict_dict()

    try:
        import jsonschema
    except ImportError:
        # jsonschema is not a declared dependency of the harness (see
        # pyproject.toml). When it is unavailable, fall back to asserting the
        # produced verdict at least carries every top-level key the schema
        # marks required.
        for key in schema["required"]:
            assert key in d, f"missing required top-level key: {key}"
        assert d["schema_version"] == 1
        return

    # Full structural validation when jsonschema is importable.
    jsonschema.validate(instance=d, schema=schema)
    # The error-path verdict (error is a non-null string) must validate too.
    jsonschema.validate(instance=Verdict.error_verdict("t", "r1", "boom").to_dict(), schema=schema)

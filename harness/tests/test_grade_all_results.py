import json
from types import SimpleNamespace

from bench.commands import grade_all as grade_all_module


class _Verdict:
    def to_dict(self):
        return {"task": "task-0001", "run_id": "run", "resolved": True, "error": None}


def test_grade_all_archives_fixed_score_and_failure_metadata(tmp_path, monkeypatch):
    task_dir = tmp_path / "tasks" / "task-0001"
    task_dir.mkdir(parents=True)
    (task_dir / "authoring").mkdir()
    patch = task_dir / "authoring" / "solution.patch"
    patch.write_text("", encoding="utf-8")
    monkeypatch.setattr(grade_all_module, "discover_task_dirs", lambda _root: [task_dir])
    monkeypatch.setattr(
        grade_all_module,
        "grade_once",
        lambda *_args, **_kwargs: SimpleNamespace(
            verdict=_Verdict(),
            raw_score=7,
            task_score=100.0,
            check_coverage=1.0,
            missing_checks=[],
            failure_class="candidate_result",
        ),
    )

    output = tmp_path / "results.jsonl"
    grade_all_module.grade_all(
        tmp_path / "tasks",
        lambda _task: patch,
        results_path=output,
    )

    record = json.loads(output.read_text(encoding="utf-8"))
    assert record["raw_score"] == 7
    assert record["task_score"] == 100.0
    assert record["check_coverage"] == 1.0
    assert record["missing_checks"] == []
    assert record["scorer_version"] == grade_all_module.SCORER_VERSION
    assert record["failure_class"] == "candidate_result"

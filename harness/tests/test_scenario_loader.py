from pathlib import Path
import sys

import pytest

from bench.verifier.scenario_loader import ScenarioError, discover_scenarios, load_scenario_module


def _make_scenarios_dir(tmp_path: Path) -> Path:
    scenarios_dir = tmp_path / "verifier" / "scenarios"
    scenarios_dir.mkdir(parents=True)
    (scenarios_dir / "__init__.py").write_text("")
    (scenarios_dir / "alpha.py").write_text(
        "ran = []\n\nasync def run(ctx):\n    ran.append(ctx)\n"
    )
    (scenarios_dir / "beta.py").write_text("async def run(ctx):\n    pass\n")
    (scenarios_dir / "no_run.py").write_text("X = 1\n")
    return tmp_path


def test_discover_scenarios_finds_all_py_files_excluding_init(tmp_path):
    task_dir = _make_scenarios_dir(tmp_path)
    paths = discover_scenarios(task_dir)
    names = sorted(p.name for p in paths)
    assert names == ["alpha.py", "beta.py", "no_run.py"]


def test_discover_scenarios_no_verifier_dir_returns_empty(tmp_path):
    assert discover_scenarios(tmp_path) == []


def test_discover_scenarios_named_subset_in_order(tmp_path):
    task_dir = _make_scenarios_dir(tmp_path)
    paths = discover_scenarios(task_dir, names=["beta", "alpha"])
    assert [p.name for p in paths] == ["beta.py", "alpha.py"]


def test_discover_scenarios_named_subset_missing_raises(tmp_path):
    task_dir = _make_scenarios_dir(tmp_path)
    with pytest.raises(ScenarioError):
        discover_scenarios(task_dir, names=["does_not_exist"])


def test_load_scenario_module_exposes_run(tmp_path):
    task_dir = _make_scenarios_dir(tmp_path)
    module = load_scenario_module(task_dir / "verifier" / "scenarios" / "alpha.py")
    assert hasattr(module, "run")


def test_load_scenario_module_without_run_raises(tmp_path):
    task_dir = _make_scenarios_dir(tmp_path)
    with pytest.raises(ScenarioError):
        load_scenario_module(task_dir / "verifier" / "scenarios" / "no_run.py")


def test_bare_helper_import_does_not_leak_between_task_directories(tmp_path):
    loaded = []
    try:
        for index, value in enumerate(("first", "second"), start=1):
            scenarios = tmp_path / f"task-{index}" / "verifier" / "scenarios"
            scenarios.mkdir(parents=True)
            (scenarios / "_shared_helper.py").write_text(f"VALUE = {value!r}\n", encoding="utf-8")
            (scenarios / "main.py").write_text(
                "import sys\n"
                "sys.path.insert(0, __file__.rsplit('/', 1)[0])\n"
                "from _shared_helper import VALUE\n"
                "async def run(ctx):\n    pass\n",
                encoding="utf-8",
            )
            module = load_scenario_module(scenarios / "main.py")
            loaded.append(module.VALUE)
        assert loaded == ["first", "second"]
    finally:
        sys.modules.pop("_shared_helper", None)

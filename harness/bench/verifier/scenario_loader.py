"""Dynamic loading of a task's verifier/scenarios/<name>.py modules.

Each scenario module must expose `async def run(ctx) -> None`. The harness
never imports verifier/ code into the agent's rollout — this loader is only
ever invoked from `bench grade` / `bench validate`.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType


class ScenarioError(RuntimeError):
    pass


def _purge_foreign_scenario_modules(scenarios_dir: Path) -> None:
    """Evict bare helper imports left behind by a previously graded task.

    Scenario files commonly import vendored siblings such as
    ``_watermark_family`` by their bare module name. A ``grade-all`` process
    loads many task directories into one interpreter, and Python otherwise
    reuses the first task's helper from ``sys.modules`` for every later task.
    Keep helpers from the current directory, but remove modules sourced from a
    different ``verifier/scenarios`` directory before executing this task.
    """
    current = scenarios_dir.resolve()
    for name, module in list(sys.modules.items()):
        source = getattr(module, "__file__", None)
        if not source:
            continue
        try:
            parent = Path(source).resolve().parent
        except OSError:
            continue
        parts = parent.parts
        if len(parts) >= 2 and parts[-2:] == ("verifier", "scenarios") and parent != current:
            sys.modules.pop(name, None)


def discover_scenarios(task_dir: Path, names: list[str] | None = None) -> list[Path]:
    """Return scenario .py file paths under task_dir/verifier/scenarios/.
    If `names` is given, only those base names (with or without .py) are
    returned, in the given order; otherwise every *.py file, sorted."""
    scenarios_dir = Path(task_dir) / "verifier" / "scenarios"
    if not scenarios_dir.is_dir():
        return []
    if names:
        paths = []
        for name in names:
            candidate = scenarios_dir / (name if name.endswith(".py") else f"{name}.py")
            if not candidate.is_file():
                raise ScenarioError(f"declared scenario not found: {candidate}")
            paths.append(candidate)
        return paths
    return sorted(p for p in scenarios_dir.glob("*.py") if p.name != "__init__.py")


def load_scenario_module(path: Path) -> ModuleType:
    _purge_foreign_scenario_modules(path.parent)
    module_name = f"bench_scenario_{path.stem}_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ScenarioError(f"could not load scenario module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    if not hasattr(module, "run"):
        raise ScenarioError(f"scenario module {path} has no `async def run(ctx)`")
    return module

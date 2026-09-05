"""Compose-unit runtime selection tests."""

from pathlib import Path

import pytest

from bench import agent_vendors
from bench.compose_unit import ComposeUnitStack, task_compose_unit_ready
from bench.config import load_task_config
from bench.eval_output import EvalDir


REPO_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_TASKS = REPO_ROOT / "tasks" / "public"


def test_compose_unit_readiness_accepts_alias_tasks() -> None:
    """Alias blocks are compose-unit eligible; they route by product.

    Supersedes the former ``preserves_alias_fallback`` assertion, which locked
    in the block-name gate as intended behavior. That was a stopgap from when
    the hosted lane could not boot these tasks at all; the hosted lane routes
    by product since pi ``523b722``, so the public lane matches it here.
    """
    assert task_compose_unit_ready(PUBLIC_TASKS / "task-0001") is True
    aliases = []
    for task_dir in sorted(PUBLIC_TASKS.glob("task-*")):
        cfg = load_task_config(task_dir)
        if any(name != meta.product for name, meta in cfg.vendors.items()):
            aliases.append(task_dir)
    assert aliases, "no vendors-block name differs from its product"
    for task_dir in aliases:
        assert task_compose_unit_ready(task_dir) is True, task_dir.name


def test_alias_tasks_still_exist() -> None:
    """Guard the guard: without an alias task the routing tests go vacuous."""
    found = []
    for task_dir in sorted(PUBLIC_TASKS.glob("task-*")):
        cfg = load_task_config(task_dir)
        found += [n for n, m in cfg.vendors.items() if n != m.product]
    assert found, "no vendors-block name differs from its product anymore"


def test_two_roles_can_run_two_instances_of_one_product(tmp_path: Path) -> None:
    (tmp_path / "task.yaml").write_text(
        "id: task-9999\ncategory: build\n"
        "vendors:\n"
        "  vendor-legacy: {}\n"
        "  vendor-new: {}\n"
        "contract:\n  vendor_roles:\n"
        "    vendor-legacy:\n      vendor_id: globalhire\n"
        "    vendor-new:\n      vendor_id: globalhire\n",
        encoding="utf-8",
    )
    assert task_compose_unit_ready(tmp_path) is True


def test_unknown_product_stays_ineligible(tmp_path: Path) -> None:
    (tmp_path / "task.yaml").write_text(
        "id: task-9998\ncategory: build\n"
        "vendors:\n  ats: {}\n"
        "contract:\n  vendor_roles:\n"
        "    ats:\n      vendor_id: not-a-real-vendor\n",
        encoding="utf-8",
    )
    assert task_compose_unit_ready(tmp_path) is False


def test_agent_vendor_boot_uses_ephemeral_compose_unit(monkeypatch, tmp_path: Path) -> None:
    calls = {}
    root = tmp_path / "stack"
    root.mkdir()

    class FakeStack:
        def __init__(self, task_dir, eval_dir, **kwargs) -> None:
            calls["task_dir"] = task_dir
            calls["eval_dir"] = eval_dir
            calls["kwargs"] = kwargs

        def up(self, *, exclude_app: bool) -> None:
            calls["exclude_app"] = exclude_app

    monkeypatch.setattr(agent_vendors, "task_compose_unit_ready", lambda _: True)
    monkeypatch.setattr(agent_vendors, "ComposeUnitStack", FakeStack)
    monkeypatch.setattr(agent_vendors.tempfile, "mkdtemp", lambda **_: str(root))
    stack = agent_vendors.start_agent_vendors(
        PUBLIC_TASKS / "task-0001",
        project="agent-test",
    )

    assert isinstance(stack, FakeStack)
    assert calls["kwargs"]["ephemeral"] is True
    assert calls["kwargs"]["include_agent"] is False
    assert calls["exclude_app"] is True


def test_failed_agent_vendor_boot_removes_ephemeral_files(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "stack"
    root.mkdir()

    class FailingStack:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def up(self, *, exclude_app: bool) -> None:
            raise RuntimeError("boot failed")

    monkeypatch.setattr(agent_vendors, "task_compose_unit_ready", lambda _: True)
    monkeypatch.setattr(agent_vendors, "ComposeUnitStack", FailingStack)
    monkeypatch.setattr(agent_vendors.tempfile, "mkdtemp", lambda **_: str(root))
    with pytest.raises(RuntimeError, match="boot failed"):
        agent_vendors.start_agent_vendors(
            PUBLIC_TASKS / "task-0001",
            project="agent-test",
        )

    assert not root.exists()


def test_kept_ephemeral_stack_retains_debug_files(tmp_path: Path) -> None:
    stack = ComposeUnitStack.__new__(ComposeUnitStack)
    stack.ephemeral = True
    stack._started = True
    stack.eval_dir = EvalDir(root=tmp_path, eval_id="agent-test")
    stack.project = "agent-test"

    stack.cleanup_override_file()
    assert tmp_path.exists()

    stack._started = False
    stack.cleanup_override_file()
    assert not tmp_path.exists()


def test_agent_up_provisions_workspace_for_fixed_container_uid(monkeypatch, tmp_path: Path) -> None:
    stack = ComposeUnitStack.__new__(ComposeUnitStack)
    stack.include_agent = True
    stack.eval_dir = EvalDir(root=tmp_path, eval_id="agent-test")
    stack.project = "agent-test"
    stack._agent_image = "agent:fixed"
    stack.vendor_services = []
    stack._started = False
    owned = []
    monkeypatch.setattr(
        "bench.compose_unit._make_claude_path_container_owned",
        lambda path, image: owned.append((path, image)),
    )
    monkeypatch.setattr(stack, "render_compose", lambda: tmp_path / "compose.yaml")
    monkeypatch.setattr(stack, "_run", lambda *args, **kwargs: None)
    monkeypatch.setattr(stack, "_discover_published_port", lambda: None)
    monkeypatch.setattr(stack, "_wait_healthy", lambda: None)

    stack.up(exclude_app=True)

    assert owned == [(stack.eval_dir.workspace, "agent:fixed")]

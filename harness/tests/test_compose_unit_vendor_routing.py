"""Standalone vendor routing and canonical task-contract tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
import yaml

from bench.compose_unit import (
    ComposeUnitStack,
    _app_env_from_task_contract,
    _app_volumes_from_task_contract,
)
from bench.compose import ComposeError, ParticipantDiskLimitExceeded
from bench.config import load_task_config
from bench.eval_output import EvalDir
from bench.images import load_images_lock

REPO_ROOT = Path(__file__).resolve().parents[2]
TASKS = REPO_ROOT / "tasks" / "public"


@pytest.fixture(autouse=True)
def _pin_vendor_images(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IB_IMAGE_MODE", "local")
    for vendor_id in load_images_lock(REPO_ROOT)["vendors"]:
        key = f"IB_VENDOR_IMAGE_{vendor_id.upper().replace('-', '_')}"
        monkeypatch.setenv(key, f"{vendor_id}:test")


def _stack(task: str, tmp_path: Path) -> ComposeUnitStack:
    root = tmp_path / task
    for relative in ("workspace/repo", "vendor-logs", "canonical-data"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    return ComposeUnitStack(
        TASKS / task,
        EvalDir(root=root, eval_id=f"routing-{task}"),
        include_agent=False,
    )


def test_all_public_vendor_products_are_locked() -> None:
    known = set(load_images_lock(REPO_ROOT)["vendors"])
    unknown = []
    for task_dir in sorted(TASKS.glob("task-*")):
        for role, meta in load_task_config(task_dir).vendors.items():
            if meta.product not in known:
                unknown.append(f"{task_dir.name}:{role}->{meta.product}")
    assert unknown == []


def test_alias_roles_render_as_independent_services(tmp_path: Path) -> None:
    stack = _stack("task-0045", tmp_path)
    compose = yaml.safe_load(stack.render_compose().read_text(encoding="utf-8"))
    services = compose["services"]

    assert services["vendor-legacy"]["image"] == "staffline:test"
    assert services["vendor-new"]["image"] == "bullpen:test"
    assert services["vendor-legacy"]["pull_policy"] == "never"
    assert services["vendor-new"]["pull_policy"] == "never"
    assert "vendors" not in services
    assert services["app"]["depends_on"] == {
        "vendor-legacy": {"condition": "service_healthy"},
        "vendor-new": {"condition": "service_healthy"},
    }
    assert list(services["app"]["networks"]) == ["workload-net"]
    assert services["vendor-legacy"]["networks"] == [
        "workload-net",
        "verifier-net",
    ]


def test_runtime_snapshot_is_role_keyed(tmp_path: Path) -> None:
    stack = _stack("task-0045", tmp_path)
    stack.render_compose()
    cfg = yaml.safe_load(stack.eval_dir.vendor_cfg_file.read_text(encoding="utf-8"))
    assert sorted(cfg) == ["vendor-legacy", "vendor-new"]
    assert cfg["vendor-legacy"]["vendor_id"] == "staffline"
    assert cfg["vendor-legacy"]["log_dir"] == "/var/log/vendor"


def test_webhook_delivery_log_uses_the_verifier_contract_name(tmp_path: Path) -> None:
    stack = _stack("task-0013", tmp_path)
    compose = yaml.safe_load(stack.render_compose().read_text(encoding="utf-8"))
    assert (
        compose["services"]["recruitos"]["environment"]["WEBHOOK_DELIVERY_LOG_PATH"]
        == "/var/log/vendor/webhook_deliveries.jsonl"
    )


def test_contract_checkpoint_seeds_each_vendor_role(tmp_path: Path) -> None:
    single = _stack("task-0003", tmp_path)
    assert single.vendor_envs["placemint"]["CHECKPOINT"] == "56"

    multi = _stack("task-0033", tmp_path)
    assert multi.vendor_envs["recruitos"]["CHECKPOINT"] == "43"
    assert multi.vendor_envs["placemint"]["CHECKPOINT"] == "56"


def test_api_and_docs_urls_use_role_dns(tmp_path: Path) -> None:
    stack = _stack("task-0045", tmp_path)
    env = stack._vendor_url_env(for_container=True)
    assert env["STAFFLINE_BASE_URL"] == "http://vendor-legacy:8000"
    assert env["STAFFLINE_DOCS_URL"] == "http://vendor-legacy:8000/_docs/"
    assert env["BULLPEN_BASE_URL"] == "http://vendor-new:8000"
    assert env["VENDOR_LEGACY_BASE_URL"] == env["STAFFLINE_BASE_URL"]


def test_each_vendor_requests_an_atomic_loopback_port(tmp_path: Path) -> None:
    stack = _stack("task-0045", tmp_path)
    compose = yaml.safe_load(stack.render_compose().read_text(encoding="utf-8"))
    legacy = compose["services"]["vendor-legacy"]["ports"][0]
    new = compose["services"]["vendor-new"]["ports"][0]
    expected = {"target": 8000, "host_ip": "127.0.0.1", "protocol": "tcp"}
    assert legacy == expected
    assert new == expected
    assert "published" not in legacy and "published" not in new
    assert "ports" not in compose["services"]["app"]


def test_persistent_state_is_unique_and_measurable_per_eval(tmp_path: Path) -> None:
    stack = _stack("task-0013", tmp_path)
    compose = yaml.safe_load(stack.render_compose().read_text(encoding="utf-8"))
    state_mount = next(
        mount
        for mount in compose["services"]["app"]["volumes"]
        if isinstance(mount, dict) and mount["target"] == "/app/state"
    )
    assert state_mount == {
        "type": "bind",
        "source": str((stack.eval_dir.participant_state_dir / "app-state").resolve()),
        "target": "/app/state",
    }
    assert Path(state_mount["source"]).is_dir()
    assert "volumes" not in compose


def test_participant_disk_budget_counts_all_retained_writable_roots(tmp_path: Path) -> None:
    stack = _stack("task-0001", tmp_path)
    stack.participant_disk_limit_bytes = 15
    (stack.eval_dir.workspace / "first").write_bytes(b"12345678")
    stack.eval_dir.participant_state_dir.mkdir(parents=True, exist_ok=True)
    (stack.eval_dir.participant_state_dir / "second").write_bytes(b"abcdefgh")

    with pytest.raises(ParticipantDiskLimitExceeded, match="16 > 15"):
        stack.assert_participant_disk_budget()


def test_multi_vendor_health_refreshes_partial_port_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = _stack("task-0045", tmp_path)
    stack.startup_timeout_s = 1.0
    discoveries = 0
    checked: list[str] = []

    def discover() -> int:
        nonlocal discoveries
        discoveries += 1
        stack.data_ports["vendor-legacy"] = 61001
        if discoveries > 1:
            stack.data_ports["vendor-new"] = 61002
        stack._published_vendor_port = 61001
        return 61001

    monkeypatch.setattr(stack, "_discover_published_port", discover)
    monkeypatch.setattr(
        "bench.compose_unit.wait_for_http",
        lambda url, **_kwargs: checked.append(url),
    )

    stack._wait_healthy()

    assert discoveries == 2
    assert checked[-2:] == [
        "http://localhost:61001/_ready",
        "http://localhost:61002/_ready",
    ]


def test_host_urls_do_not_include_mega_routing_paths(tmp_path: Path) -> None:
    stack = _stack("task-0045", tmp_path)
    stack.data_ports.update({"vendor-legacy": 54321, "staffline": 54321})
    assert stack.data_base_url_for("vendor-legacy") == "http://localhost:54321"
    assert stack.data_base_url_for("staffline") == "http://localhost:54321"


def test_checkpoint_recreate_is_compose_owned(tmp_path: Path, monkeypatch) -> None:
    stack = _stack("task-0045", tmp_path)
    stack.vendor_envs["vendor-legacy"]["CHECKPOINT"] = "2"
    calls: list[list[str]] = []

    def fake_run(cmd, check=True, **_kwargs):
        calls.append(list(cmd))
        if "port" in cmd:
            service = cmd[cmd.index("port") + 1]
            port = 61001 if service == "vendor-legacy" else 61002
            return subprocess.CompletedProcess(cmd, 0, stdout=f"0.0.0.0:{port}\n", stderr="")
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(stack, "_run", fake_run)
    monkeypatch.setattr("bench.compose_unit.wait_for_http", lambda *_a, **_k: None)
    stack.recreate_vendor("staffline")

    recreate = next(cmd for cmd in calls if "--force-recreate" in cmd)
    assert recreate[-3:] == ["--force-recreate", "--no-deps", "vendor-legacy"]
    rendered = yaml.safe_load(stack.eval_dir.compose_file.read_text(encoding="utf-8"))
    assert rendered["services"]["vendor-legacy"]["environment"]["CHECKPOINT"] == "2"
    assert all("/_ib/" not in " ".join(cmd) for cmd in calls)


def test_vendor_exec_runs_inside_role_container(tmp_path: Path, monkeypatch) -> None:
    stack = _stack("task-0001", tmp_path)
    seen: list[list[str]] = []

    def fake_run(cmd, check=True, **_kwargs):
        seen.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(stack, "_run", fake_run)
    stack.exec("staffline", "python", "-c", "print('ok')")
    assert seen[0][-6:] == ["exec", "-T", "staffline", "python", "-c", "print('ok')"]


def test_vendor_log_reads_resolve_to_role_dir(tmp_path: Path) -> None:
    stack = _stack("task-0045", tmp_path)
    log_dir = stack.eval_dir.vendor_logs_dir / "vendor-legacy"
    log_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "requests.jsonl").write_text('{"path": "/candidates"}\n', encoding="utf-8")
    assert stack.read_vendor_jsonl("staffline", "requests.jsonl") == [{"path": "/candidates"}]


def test_task_app_environment_is_read_from_contract(tmp_path: Path) -> None:
    (tmp_path / "task.yaml").write_text(
        "contract:\n  participant:\n    runtime_environment:\n"
        "      REVIEWER_EMAIL: review@example.test\n",
        encoding="utf-8",
    )
    assert _app_env_from_task_contract(tmp_path) == {"REVIEWER_EMAIL": "review@example.test"}


def test_task_app_named_volume_is_preserved_from_contract(tmp_path: Path) -> None:
    (tmp_path / "task.yaml").write_text(
        "contract:\n  participant:\n    persistent_mounts:\n      - app-state:/app/state\n",
        encoding="utf-8",
    )
    mounts, declarations = _app_volumes_from_task_contract(tmp_path)
    assert mounts == ["app-state:/app/state"]
    assert declarations == {"app-state": None}


@pytest.mark.parametrize(
    "mount",
    [
        "/:/host",
        "../host:/app/state",
        "docker-sock:/var/run/docker.sock",
    ],
)
def test_task_persistent_mounts_cannot_escape_project_volumes(tmp_path: Path, mount: str) -> None:
    (tmp_path / "task.yaml").write_text(
        f"contract:\n  participant:\n    persistent_mounts:\n      - {mount}\n",
        encoding="utf-8",
    )
    with pytest.raises(ComposeError):
        _app_volumes_from_task_contract(tmp_path)


def test_task_mounts_do_not_shadow_harness_state_binds() -> None:
    mounts, named = _app_volumes_from_task_contract(TASKS / "task-0012")
    assert all(not (isinstance(mount, str) and mount.split(":")[1] == "/data") for mount in mounts)
    assert "canonical-data" not in named

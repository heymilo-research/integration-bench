"""Fail-closed egress proxy and Compose network policy tests."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from bench.compose_unit import ComposeUnitStack, _agent_egress_hosts
from bench.egress_proxy import parse_allowlist, parse_connect_target, target_allowed
from bench.eval_output import EvalDir

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_allowlist_is_exact_and_rejects_ip_literals() -> None:
    allowed = parse_allowlist("api.openai.com, CHATGPT.com. ")
    assert allowed == frozenset({"api.openai.com", "chatgpt.com"})
    assert target_allowed("api.openai.com", 443, allowed)
    assert not target_allowed("evil.api.openai.com", 443, allowed)
    assert not target_allowed("api.openai.com", 80, allowed)
    with pytest.raises(ValueError):
        parse_allowlist("127.0.0.1")


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("api.openai.com:443", ("api.openai.com", 443)),
        ("CHATGPT.COM.:443", ("chatgpt.com", 443)),
    ],
)
def test_connect_target_parser(target: str, expected: tuple[str, int]) -> None:
    assert parse_connect_target(target) == expected


@pytest.mark.parametrize("target", ["127.0.0.1:443", "[::1]:443", "host", "host:x"])
def test_connect_target_parser_rejects_unsafe_forms(target: str) -> None:
    with pytest.raises(ValueError):
        parse_connect_target(target)


def _render_codex_compose(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict:
    monkeypatch.setenv("IB_IMAGE_MODE", "local")
    monkeypatch.setenv("IB_VENDOR_IMAGE_STAFFLINE", "staffline:test")
    monkeypatch.setattr("bench.compose_unit.ensure_agent_image", lambda **_: "ib-agent:codex-test")
    monkeypatch.setattr(
        "bench.compose_unit._codex_agent_extras",
        lambda *_args, **_kwargs: ([], {"CODEX_HOME": "/codex-home"}),
    )
    root = tmp_path / "eval"
    for relative in ("workspace/repo", "vendor-logs", "canonical-data"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    stack = ComposeUnitStack(
        REPO_ROOT / "tasks" / "public" / "task-0001",
        EvalDir(root=root, eval_id="egress-policy"),
        include_agent=True,
        agent_codex=True,
    )
    return yaml.safe_load(stack.render_compose().read_text(encoding="utf-8"))


def test_agent_and_app_are_internal_and_only_proxy_is_dual_homed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    compose = _render_codex_compose(monkeypatch, tmp_path)
    services = compose["services"]
    assert compose["networks"]["workload-net"] == {"internal": True}
    assert compose["networks"]["verifier-net"] == {}
    assert compose["networks"]["egress-net"] == {}
    assert services["agent"]["networks"] == ["workload-net"]
    assert list(services["app"]["networks"]) == ["workload-net"]
    assert services["staffline"]["networks"] == ["workload-net", "verifier-net"]
    assert services["egress-proxy"]["networks"] == ["workload-net", "egress-net"]
    assert services["agent"]["pull_policy"] == "never"
    assert services["egress-proxy"]["pull_policy"] == "never"


def test_codex_proxy_environment_and_exact_hosts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    compose = _render_codex_compose(monkeypatch, tmp_path)
    agent = compose["services"]["agent"]
    proxy = compose["services"]["egress-proxy"]
    assert agent["environment"]["HTTPS_PROXY"] == "http://egress-proxy:3128"
    assert "staffline" in agent["environment"]["NO_PROXY"].split(",")
    assert set(proxy["environment"]["IB_EGRESS_ALLOWLIST"].split(",")) == {
        "api.openai.com",
        "auth.openai.com",
        "chatgpt.com",
    }
    assert proxy["read_only"] is True
    assert proxy["cap_drop"] == ["ALL"]


def test_rendered_stack_enforces_non_root_least_privilege_and_task_limits(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    compose = _render_codex_compose(monkeypatch, tmp_path)
    services = compose["services"]

    for name in ("staffline", "app", "agent", "egress-proxy"):
        service = services[name]
        assert service["read_only"] is True
        assert service["cap_drop"] == ["ALL"]
        assert service["security_opt"] == ["no-new-privileges:true"]
        assert service["pids_limit"] == 512
        assert service["init"] is True
        assert not str(service["user"]).startswith("0:")
        assert all("docker.sock" not in str(volume) for volume in service.get("volumes", []))

    # Candidate-controlled services receive the task-declared participant
    # budget in both work and grade phases.
    for name in ("app", "agent"):
        service = services[name]
        assert service["cpus"] == 2.0
        assert service["mem_limit"] == "2g"
        assert service["tmpfs"] == ["/tmp:size=2g,mode=1777"]

    # Trusted benchmark infrastructure is separately and tightly bounded.
    for name in ("staffline", "egress-proxy"):
        service = services[name]
        assert service["cpus"] == 1.0
        assert service["mem_limit"] == "512m"
        assert service["tmpfs"] == ["/tmp:size=256m,mode=1777"]

    agent_sources = [str(volume).split(":", 1)[0] for volume in services["agent"]["volumes"]]
    assert agent_sources == [str((tmp_path / "eval" / "workspace").resolve())]


def test_lane_allowlists_do_not_include_code_or_package_hosts() -> None:
    for kwargs in (
        {"claude_code": True, "codex": False, "opencode": False},
        {"claude_code": False, "codex": True, "opencode": False},
        {"claude_code": False, "codex": False, "opencode": True},
    ):
        hosts = _agent_egress_hosts(**kwargs)
        assert "github.com" not in hosts
        assert "pypi.org" not in hosts
        assert "npmjs.com" not in hosts

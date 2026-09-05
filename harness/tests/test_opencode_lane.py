from pathlib import Path

import yaml

from bench.commands.eval_core import _opencode_usage_and_cost, _remove_ephemeral_agent_home
from bench.compose_unit import ComposeUnitStack, _opencode_model_config
from bench.eval_output import EvalDir
from bench.provenance import _effective_reasoning_effort


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_opencode_step_finish_usage_and_cost():
    event = {
        "type": "step_finish",
        "part": {
            "tokens": {
                "input": 100,
                "output": 20,
                "reasoning": 7,
                "cache": {"read": 80, "write": 4},
            },
            "cost": 0.0123,
        },
    }
    usage, cost = _opencode_usage_and_cost(event)
    assert usage == {
        "input_tokens": 100,
        "cached_input_tokens": 80,
        "cache_write_input_tokens": 4,
        "output_tokens": 20,
        "reasoning_output_tokens": 7,
    }
    assert cost == 0.0123


def test_opencode_variant_provenance_is_literal():
    assert _effective_reasoning_effort("opencode", "high") == ("high", "opencode --variant high")
    assert _effective_reasoning_effort("opencode", "xhigh") == ("xhigh", "opencode --variant xhigh")


def test_opencode_selected_model_is_registered_in_isolated_config():
    assert _opencode_model_config("openrouter/google/gemini-3.7-flash") == {
        "$schema": "https://opencode.ai/config.json",
        "provider": {
            "openrouter": {
                "options": {
                    "apiKey": "integration-bench-provider-gateway",
                    "baseURL": "http://openrouter-gateway:8080/api/v1",
                },
                "models": {"google/gemini-3.7-flash": {}},
            }
        },
    }


def test_opencode_provider_key_is_available_only_to_gateway(monkeypatch, tmp_path):
    real_key = "synthetic-openrouter-secret-for-isolation-test"
    monkeypatch.setenv("OPENROUTER_API_KEY", real_key)
    monkeypatch.setenv("IB_IMAGE_MODE", "local")
    monkeypatch.setenv("IB_VENDOR_IMAGE_STAFFLINE", "staffline:test")
    monkeypatch.setattr(
        "bench.compose_unit.ensure_agent_image", lambda **_: "ib-agent:opencode-test"
    )
    monkeypatch.setattr("bench.compose_unit._make_claude_path_container_owned", lambda *_: None)
    root = tmp_path / "eval"
    for relative in ("workspace/repo", "vendor-logs", "canonical-data"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    eval_dir = EvalDir(root=root, eval_id="opencode-isolation")
    stack = ComposeUnitStack(
        REPO_ROOT / "tasks" / "public" / "task-0001",
        eval_dir,
        include_agent=True,
        agent_opencode=True,
        opencode_model="meta/muse-spark-1.2",
    )

    compose_path = stack.render_compose()
    rendered = compose_path.read_text(encoding="utf-8")
    compose = yaml.safe_load(rendered)
    agent = compose["services"]["agent"]
    gateway = compose["services"]["openrouter-gateway"]

    assert real_key not in rendered
    assert "env_file" not in agent
    assert "OPENROUTER_API_KEY" not in agent["environment"]
    assert "openrouter-gateway" in agent["environment"]["NO_PROXY"].split(",")
    assert gateway["networks"] == ["workload-net", "egress-net"]
    assert gateway["env_file"] == [str((root / "provider-auth" / "provider.env").resolve())]
    assert "IB_PROVIDER_ONLY" not in gateway["environment"]
    assert all("provider-auth" not in volume for volume in agent["volumes"])

    config = root / "agent-home" / "opencode" / "config" / "opencode" / "opencode.json"
    assert real_key not in config.read_text(encoding="utf-8")
    assert "integration-bench-provider-gateway" in config.read_text(encoding="utf-8")

    _remove_ephemeral_agent_home(eval_dir)
    assert not (root / "agent-home").exists()
    assert not (root / "provider-auth").exists()


def test_gemini_opencode_lane_routes_only_to_google_vertex(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENROUTER_API_KEY", "synthetic-openrouter-secret")
    monkeypatch.setenv("IB_IMAGE_MODE", "local")
    monkeypatch.setenv("IB_VENDOR_IMAGE_STAFFLINE", "staffline:test")
    monkeypatch.setattr(
        "bench.compose_unit.ensure_agent_image", lambda **_: "ib-agent:opencode-test"
    )
    monkeypatch.setattr("bench.compose_unit._make_claude_path_container_owned", lambda *_: None)
    root = tmp_path / "eval"
    for relative in ("workspace/repo", "vendor-logs", "canonical-data"):
        (root / relative).mkdir(parents=True, exist_ok=True)
    stack = ComposeUnitStack(
        REPO_ROOT / "tasks" / "public" / "task-0001",
        EvalDir(root=root, eval_id="gemini-vertex-route"),
        include_agent=True,
        agent_opencode=True,
        opencode_model="google/gemini-3.7-flash",
    )

    compose = yaml.safe_load(stack.render_compose().read_text(encoding="utf-8"))
    assert (
        compose["services"]["openrouter-gateway"]["environment"]["IB_PROVIDER_ONLY"]
        == "google-vertex"
    )
    assert (
        compose["services"]["openrouter-gateway"]["environment"]["IB_SANITIZE_GOOGLE_TOOL_OUTPUTS"]
        == "1"
    )

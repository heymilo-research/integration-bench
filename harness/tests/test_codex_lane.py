"""Codex lane: usage-limit detection, event rendering, auth + image wiring.

The heavyweight test here is the false-positive suite on
``detect_codex_usage_limit``. Integration-Bench is a benchmark ABOUT rate
limiting, so a Codex rollout solving task-0022 or task-0047 prints "429",
"rate limit" and "Retry-After" constantly — in reasoning text, in executed curl
output, and in the connector source it writes. A detector that matched any of
those would mark SUCCESSFUL runs on the suite's most interesting tasks as
`usage_limit`, refuse to grade them, and silently drop them from the sweep.
That is a data-integrity bug that reads as a scheduling hiccup, so it gets
pinned here rather than left to review.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bench.commands.eval_core import (
    _codex_event_lines,
    detect_codex_usage_limit,
    eval_codex_once,
)
from bench.compose_unit import (
    CODEX_AGENT_IMAGE,
    EvalDir,
    _codex_agent_extras,
    _valid_codex_auth_file,
    codex_agent_dockerfile,
)


# --------------------------------------------------------------------------
# usage-limit detection
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "You've hit your usage limit for GPT-5.6 Sol.",
        "You’ve hit your usage limit for this model.",  # curly apostrophe
        "you have hit your usage limit",
        "YOU'VE HIT YOUR USAGE LIMIT",
        '{"type":"usage_limit_reached","plan":"plus"}',
    ],
)
def test_detects_real_plan_exhaustion(text: str) -> None:
    hit, _ = detect_codex_usage_limit(text)
    assert hit is True


@pytest.mark.parametrize(
    "text",
    [
        # Vendor 429s: the actual subject matter of task-0022 / task-0047.
        "HTTP/1.1 429 Too Many Requests",
        "vendor returned 429, backing off per Retry-After: 30",
        "the API enforces a rate limit of 25 requests per minute",
        "Rate limit exceeded; sleeping 2s before retry",
        "raise RateLimitError('rate limit reached')",
        # Connector source the agent writes into repo/.
        "if resp.status_code == 429:  # rate limited, honour Retry-After",
        "# hit the rate limit budget, so pace the crawl",
        # Adjacent-but-different Codex wording that must not trip it either.
        "Reasoning: I should avoid hitting the vendor's usage limits here.",
        "Usage: codex exec [OPTIONS] [PROMPT]",
        "",
    ],
)
def test_ignores_benchmark_rate_limit_chatter(text: str) -> None:
    hit, reset = detect_codex_usage_limit(text)
    assert hit is False, f"false positive on: {text!r}"
    assert reset is None


def test_reset_epoch_parsed_only_from_machine_readable_field() -> None:
    hit, reset = detect_codex_usage_limit('{"type":"usage_limit_reached","resets_at":1786000000}')
    assert hit is True
    assert reset == 1786000000

    # Prose without the JSON field yields no epoch rather than a guessed one.
    hit, reset = detect_codex_usage_limit("You've hit your usage limit; try again at 4pm")
    assert hit is True
    assert reset is None


# --------------------------------------------------------------------------
# JSONL event rendering
# --------------------------------------------------------------------------


def test_renders_the_event_shapes_a_real_run_emits() -> None:
    # Captured from codex-cli 0.146.0 (`codex exec --json --ephemeral`).
    assert _codex_event_lines({"type": "thread.started", "thread_id": "abc"}) == [
        "[init] thread=abc"
    ]
    assert _codex_event_lines({"type": "turn.started"}) == []
    assert _codex_event_lines(
        {
            "type": "item.completed",
            "item": {"id": "item_0", "type": "agent_message", "text": "OK"},
        }
    ) == ["[assistant] OK"]
    assert _codex_event_lines(
        {
            "type": "turn.completed",
            "usage": {
                "input_tokens": 21555,
                "cached_input_tokens": 0,
                "output_tokens": 5,
            },
        }
    ) == ["[done] in=21555 cached=0 out=5"]


def test_renders_command_execution_as_shell_transcript() -> None:
    started = _codex_event_lines(
        {
            "type": "item.started",
            "item": {"type": "command_execution", "command": "curl -s $VENDOR_BASE_URL"},
        }
    )
    assert started == ["$ curl -s $VENDOR_BASE_URL"]

    done = _codex_event_lines(
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "curl -s $VENDOR_BASE_URL",
                "exit_code": 0,
                "aggregated_output": '{"ok":true}',
            },
        }
    )
    assert done == ['[result] exit=0 {"ok":true}']


def test_file_change_renders_once_not_twice() -> None:
    """Codex emits file_change as a started/completed pair with the same paths.

    Rendering both duplicated every edit line in the agent log — observed on the
    2026-08-10 container canary, where one write produced two identical `[edit]`
    lines. command_execution is the deliberate exception: its pair renders as
    `$ cmd` then `[result]`, which are two different lines.
    """
    item = {"type": "file_change", "changes": [{"path": "/workspace/repo/answer.txt"}]}
    assert _codex_event_lines({"type": "item.started", "item": item}) == []
    assert _codex_event_lines({"type": "item.completed", "item": item}) == [
        "[edit] /workspace/repo/answer.txt"
    ]


def test_turn_failed_is_rendered_and_unknown_events_are_dropped() -> None:
    assert _codex_event_lines(
        {"type": "turn.failed", "error": {"message": "model overloaded"}}
    ) == ["[failed] model overloaded"]
    assert _codex_event_lines({"type": "item.completed", "item": {"type": "wat"}}) == []
    assert _codex_event_lines({"type": "some.future.event"}) == []


# --------------------------------------------------------------------------
# auth.json validation
# --------------------------------------------------------------------------


def test_auth_modes_are_distinguished(tmp_path: Path) -> None:
    chatgpt = tmp_path / "chatgpt.json"
    chatgpt.write_text(
        json.dumps(
            {
                "auth_mode": "chatgpt",
                "OPENAI_API_KEY": None,
                "tokens": {
                    "access_token": "a",
                    "refresh_token": "r",
                    "account_id": "acct",
                },
            }
        )
    )
    assert _valid_codex_auth_file(chatgpt) == (True, "chatgpt")

    api = tmp_path / "api.json"
    api.write_text(json.dumps({"OPENAI_API_KEY": "sk-test"}))
    assert _valid_codex_auth_file(api) == (True, "api_key")


@pytest.mark.parametrize(
    "payload",
    [
        "{}",
        "not json at all",
        '{"tokens": {"access_token": "a"}}',  # refresh token missing
        '{"tokens": {}, "OPENAI_API_KEY": ""}',
        "[]",
    ],
)
def test_unusable_auth_files_are_rejected(tmp_path: Path, payload: str) -> None:
    p = tmp_path / "auth.json"
    p.write_text(payload)
    assert _valid_codex_auth_file(p) == (False, "")


def test_missing_auth_file_is_rejected(tmp_path: Path) -> None:
    assert _valid_codex_auth_file(tmp_path / "nope.json") == (False, "")


def test_codex_rollout_gets_fresh_minimal_home(monkeypatch, tmp_path: Path) -> None:
    host = tmp_path / "host-codex"
    host.mkdir()
    (host / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": "a", "refresh_token": "r"}})
    )
    (host / "config.toml").write_text("model = 'host-secret-model'\n")
    (host / "memories").mkdir()
    (host / "memories" / "MEMORY.md").write_text("contamination")
    monkeypatch.setenv("IB_CODEX_HOME", str(host))
    ownership: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        "bench.compose_unit._make_claude_path_container_owned",
        lambda path, image: ownership.append((path, image)),
    )
    root = tmp_path / "eval"
    (root / "workspace").mkdir(parents=True)
    ed = EvalDir(root=root, eval_id="0" * 32)

    volumes, env = _codex_agent_extras(ed, agent_image="codex:test")

    isolated = root / "agent-home" / "codex"
    assert volumes == [f"{isolated.resolve()}:/codex-home"]
    assert json.loads((isolated / "auth.json").read_text())["tokens"]["access_token"] == "a"
    assert (isolated / "auth.json").stat().st_mode & 0o777 == 0o600
    assert (isolated / "config.toml").read_text() == ""
    assert not (isolated / "memories").exists()
    assert ownership == [(isolated, "codex:test")]
    assert env["CODEX_HOME"] == "/codex-home"
    assert env["IB_CODEX_AUTH_MODE"] == "chatgpt"


def test_codex_eval_provisions_agent_home_once(monkeypatch, tmp_path: Path) -> None:
    """Compose up owns rendering; a pre-render would create the home twice."""

    class Started(Exception):
        pass

    class FakeStack:
        _agent_image = "codex:test"

        def __init__(self, *args, **kwargs) -> None:
            pass

        def render_compose(self) -> None:
            raise AssertionError("eval must not render before up()")

        def up(self, *, exclude_app: bool) -> None:
            assert exclude_app is True
            raise Started

    eval_dir = SimpleNamespace(
        workspace=tmp_path,
        write_meta=lambda payload: None,
    )
    monkeypatch.setattr("bench.commands.eval_core.load_dotenv_files", lambda: None)
    monkeypatch.setattr(
        "bench.commands.eval_core.load_task_config",
        lambda task_dir: SimpleNamespace(id="task-0018"),
    )
    monkeypatch.setattr("bench.commands.eval_core.EvalDir.create", lambda **kwargs: eval_dir)
    monkeypatch.setattr(
        "bench.commands.eval_core.stage_workspace_into_eval", lambda task_dir, ed: tmp_path
    )
    monkeypatch.setattr("bench.commands.eval_core.ComposeUnitStack", FakeStack)
    monkeypatch.setattr("bench.commands.eval_core.capture_provenance", lambda *args, **kwargs: {})
    monkeypatch.setattr("bench.commands.eval_core.capture_image_identity", lambda image: {})
    monkeypatch.setattr("bench.commands.eval_core.codex_auth_mode", lambda: "chatgpt")

    with pytest.raises(Started):
        eval_codex_once(tmp_path, model="gpt-5.6-luna")


# --------------------------------------------------------------------------
# image isolation — the property that protects a live Claude Code sweep
# --------------------------------------------------------------------------


def test_codex_image_is_a_separate_tag_and_dockerfile() -> None:
    """A codex Dockerfile edit must never rebuild `ib-agent:local`.

    `ensure_agent_image()` decides staleness by hashing the Dockerfile bytes
    against the image's `ib.agent.dockerfile_sha` label. Sharing either the tag
    or the file between lanes would make an edit for one lane silently rebuild
    the other's agent image on its next rollout — mid-sweep, with no error.
    """
    from bench.compose_unit import AGENT_IMAGE, agent_dockerfile

    assert CODEX_AGENT_IMAGE != AGENT_IMAGE
    assert codex_agent_dockerfile() != agent_dockerfile()
    assert codex_agent_dockerfile().is_file()
    assert "@openai/codex" in codex_agent_dockerfile().read_text()
    # The Claude lane's image must not carry codex, nor vice versa: a shared
    # image would put both CLIs on PATH and make the lane label unfalsifiable.
    assert "@openai/codex" not in agent_dockerfile().read_text()
    assert "@anthropic-ai/claude-code" not in codex_agent_dockerfile().read_text()


def test_agent_uid_matches_across_lanes() -> None:
    """Both agent images must own graded files identically."""
    from bench.compose_unit import agent_dockerfile

    assert "useradd -m -s /bin/bash -u 1001 agent" in agent_dockerfile().read_text()
    assert "useradd -m -s /bin/bash -u 1001 agent" in codex_agent_dockerfile().read_text()

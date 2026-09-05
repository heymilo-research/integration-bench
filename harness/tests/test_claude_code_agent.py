from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

import pytest

import bench.compose_unit as compose_unit
from bench.compose import ComposeError


def _oauth_json(expires_in_ms: int = 3_600_000) -> str:
    """A credentials envelope. Default: valid for another hour.

    ``expiresAt`` is not decorative — the seeder refuses to copy a dead token
    into the container, because an expired credential grades as 0.0 on every
    task and reads as a bad model rather than a broken lane.
    """
    return json.dumps(
        {
            "claudeAiOauth": {
                "accessToken": "test-access",
                "refreshToken": "test-refresh",
                "expiresAt": int(time.time() * 1000) + expires_in_ms,
            }
        }
    )


def test_macos_keychain_seeds_linux_credentials(monkeypatch, tmp_path: Path) -> None:
    work_dir = tmp_path / ".claude-work"
    work_dir.mkdir()
    credentials = work_dir / ".credentials.json"
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout=_oauth_json(), stderr="")

    monkeypatch.setattr(compose_unit.sys, "platform", "darwin")
    monkeypatch.setattr(compose_unit.subprocess, "run", fake_run)

    compose_unit._seed_claude_credentials_from_macos_keychain(work_dir, credentials)

    expected_hash = hashlib.sha256(str(work_dir.resolve()).encode()).hexdigest()[:8]
    assert calls == [
        [
            "security",
            "find-generic-password",
            "-s",
            f"Claude Code-credentials-{expected_hash}",
            "-w",
        ]
    ]
    assert compose_unit._valid_claude_oauth_file(credentials)
    assert credentials.stat().st_mode & 0o777 == 0o600


def test_valid_credentials_do_not_read_keychain(monkeypatch, tmp_path: Path) -> None:
    work_dir = tmp_path / ".claude-work"
    work_dir.mkdir()
    credentials = work_dir / ".credentials.json"
    credentials.write_text(_oauth_json(), encoding="utf-8")

    monkeypatch.setattr(compose_unit.sys, "platform", "darwin")

    def fail_run(*args, **kwargs):
        raise AssertionError("valid credentials must not read Keychain")

    monkeypatch.setattr(compose_unit.subprocess, "run", fail_run)
    compose_unit._seed_claude_credentials_from_macos_keychain(work_dir, credentials)


def test_expired_file_is_reseeded_from_keychain(monkeypatch, tmp_path: Path) -> None:
    """Regression: a shape-valid but EXPIRED file must not short-circuit the seed.

    The original check asked only whether accessToken/refreshToken were
    non-empty, so once a stale file existed it was mounted forever and every
    rollout reported "Not logged in" while the Keychain held a good token.
    """
    work_dir = tmp_path / ".claude-work"
    work_dir.mkdir()
    credentials = work_dir / ".credentials.json"
    credentials.write_text(_oauth_json(expires_in_ms=-1), encoding="utf-8")

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=_oauth_json(), stderr="")

    monkeypatch.setattr(compose_unit.sys, "platform", "darwin")
    monkeypatch.setattr(compose_unit.subprocess, "run", fake_run)

    compose_unit._seed_claude_credentials_from_macos_keychain(work_dir, credentials)

    assert not compose_unit._claude_oauth_expired(credentials)


def test_expired_keychain_token_fails_loudly(monkeypatch, tmp_path: Path) -> None:
    """Regression: never copy a dead Keychain token into the container.

    Silently seeding an expired token is how a 50-task sweep scores ~0 and gets
    misread as model capability.
    """
    work_dir = tmp_path / ".claude-work"
    work_dir.mkdir()
    credentials = work_dir / ".credentials.json"

    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 0, stdout=_oauth_json(expires_in_ms=-1), stderr="")

    monkeypatch.setattr(compose_unit.sys, "platform", "darwin")
    monkeypatch.setattr(compose_unit.subprocess, "run", fake_run)

    with pytest.raises(ComposeError, match="EXPIRED"):
        compose_unit._seed_claude_credentials_from_macos_keychain(work_dir, credentials)
    assert not credentials.exists()


def test_stream_events_render_progress_lines() -> None:
    from bench.commands.eval_core import _cc_event_lines

    assert _cc_event_lines(
        {"type": "system", "subtype": "init", "model": "sonnet", "cwd": "/workspace"}
    ) == ["[init] model=sonnet cwd=/workspace"]

    assert _cc_event_lines(
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Reading docs"},
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {"command": "ls /workspace"},
                    },
                ]
            },
        }
    ) == ["[assistant] Reading docs", "[tool] Bash command=ls /workspace"]

    assert _cc_event_lines(
        {"type": "result", "subtype": "success", "num_turns": 7, "duration_ms": 12}
    ) == ["[done] subtype=success turns=7 duration_ms=12 is_error=None"]


def test_agent_extras_use_work_profile_credentials(monkeypatch, tmp_path: Path) -> None:
    work_dir = tmp_path / ".claude-work"
    work_dir.mkdir()
    (work_dir / ".credentials.json").write_text(_oauth_json(), encoding="utf-8")
    (work_dir / ".claude.json").write_text(
        json.dumps({"projects": {"/secret/project": {"lastSessionId": "leak"}}}),
        encoding="utf-8",
    )
    (work_dir / "settings.json").write_text(
        json.dumps({"model": "host-model", "enabledPlugins": {"private": True}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("IB_CLAUDE_WORK", str(work_dir))
    monkeypatch.delenv("IB_CLAUDE_CREDENTIALS", raising=False)
    ownership_calls: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        compose_unit,
        "_make_claude_path_container_owned",
        lambda path, image: ownership_calls.append((path, image)),
    )

    eval_root = tmp_path / "eval"
    eval_root.mkdir()
    ed = compose_unit.EvalDir(root=eval_root, eval_id="0" * 32)
    volumes, env = compose_unit._claude_code_agent_extras(ed)

    isolated = eval_root / "agent-home" / "claude"
    assert volumes == [f"{isolated.resolve()}:/claude-work"]
    assert (isolated / ".credentials.json").is_file()
    assert json.loads((isolated / ".claude.json").read_text()) == {
        "hasCompletedOnboarding": True,
        "autoUpdates": False,
    }
    assert json.loads((isolated / "settings.json").read_text()) == {
        "skipDangerousModePermissionPrompt": True,
    }
    assert not (isolated / "projects").exists()
    assert env == {
        "CLAUDE_CONFIG_DIR": "/claude-work",
        "HOME": "/home/agent",
    }
    assert ownership_calls == [(isolated, compose_unit.AGENT_IMAGE)]


def test_native_linux_claude_path_is_chowned_inside_agent_image(
    monkeypatch, tmp_path: Path
) -> None:
    isolated = tmp_path / "claude"
    isolated.mkdir()
    credential = isolated / ".credentials.json"
    credential.write_text(_oauth_json(), encoding="utf-8")
    credential.chmod(0o600)
    calls: list[tuple[list[str], dict]] = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(compose_unit.sys, "platform", "linux")
    monkeypatch.setattr(compose_unit.subprocess, "run", fake_run)

    compose_unit._make_claude_path_container_owned(isolated, "ib-agent:test")

    assert calls[0][0] == [
        "docker",
        "run",
        "--rm",
        "--user",
        "0:0",
        "--volume",
        f"{isolated.resolve()}:/target",
        "--entrypoint",
        "sh",
        "ib-agent:test",
        "-c",
        'chown -R "$(id -u agent):$(id -g agent)" /target',
    ]
    assert credential.stat().st_mode & 0o777 == 0o600


def test_native_linux_claude_path_chown_failure_is_fatal(monkeypatch, tmp_path: Path) -> None:
    isolated = tmp_path / "claude"
    isolated.mkdir()
    monkeypatch.setattr(compose_unit.sys, "platform", "linux")
    monkeypatch.setattr(
        compose_unit.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(
            argv, 125, stdout="", stderr="daemon unavailable"
        ),
    )

    with pytest.raises(ComposeError, match="daemon unavailable"):
        compose_unit._make_claude_path_container_owned(isolated, "ib-agent:test")


def test_locked_agent_image_is_used_without_rebuild(monkeypatch, tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="[]", stderr="")

    monkeypatch.setenv("IB_AGENT_IMAGE_LOCKED", "1")
    monkeypatch.setattr(compose_unit.subprocess, "run", fake_run)

    assert (
        compose_unit.ensure_agent_image(tag="registry/agent@sha256:abc", dockerfile=dockerfile)
        == "registry/agent@sha256:abc"
    )
    assert calls == [["docker", "image", "inspect", "registry/agent@sha256:abc"]]


def test_missing_locked_agent_image_fails_instead_of_building(monkeypatch, tmp_path: Path) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n", encoding="utf-8")
    monkeypatch.setenv("IB_AGENT_IMAGE_LOCKED", "1")
    monkeypatch.setattr(
        compose_unit.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, stdout="", stderr="not found"),
    )

    with pytest.raises(ComposeError, match="locked agent image is not present"):
        compose_unit.ensure_agent_image(tag="registry/agent@sha256:missing", dockerfile=dockerfile)


def test_completed_rollout_removes_credentials_but_keeps_evidence(tmp_path: Path) -> None:
    from bench.commands.eval_core import _remove_ephemeral_agent_home

    ed = compose_unit.EvalDir(root=tmp_path / "eval", eval_id="0" * 32)
    home = ed.root / "agent-home" / "claude"
    home.mkdir(parents=True)
    (home / ".credentials.json").write_text(_oauth_json(), encoding="utf-8")
    (home / "projects").mkdir()
    evidence = ed.root / "transcript.jsonl"
    evidence.write_text("{}\n", encoding="utf-8")

    _remove_ephemeral_agent_home(ed)

    assert not (ed.root / "agent-home").exists()
    assert evidence.read_text(encoding="utf-8") == "{}\n"


def test_linux_cleanup_returns_container_owned_home_to_host(monkeypatch, tmp_path: Path) -> None:
    from bench.commands.eval_core import _remove_ephemeral_agent_home

    ed = compose_unit.EvalDir(root=tmp_path / "eval", eval_id="0" * 32)
    home = ed.root / "agent-home" / "claude" / "sessions"
    home.mkdir(parents=True)
    (home / "state.json").write_text("{}\n", encoding="utf-8")
    calls: list[list[str]] = []

    monkeypatch.setattr(compose_unit.sys, "platform", "linux")
    monkeypatch.setattr(
        compose_unit.subprocess,
        "run",
        lambda argv, **kwargs: (
            calls.append(argv) or subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        ),
    )

    _remove_ephemeral_agent_home(ed, agent_image="registry/agent@sha256:fixed")

    assert not (ed.root / "agent-home").exists()
    assert calls and calls[0][:5] == ["docker", "run", "--rm", "--user", "0:0"]
    assert "registry/agent@sha256:fixed" in calls[0]


def test_cleanup_failure_does_not_mask_completed_model_run(monkeypatch, tmp_path: Path) -> None:
    from bench.commands.eval_core import _cleanup_ephemeral_agent_home

    ed = compose_unit.EvalDir(root=tmp_path / "eval", eval_id="0" * 32)
    (ed.root / "agent-home").mkdir(parents=True)
    ed.workspace.mkdir(parents=True)
    stack = type("Stack", (), {"_agent_image": "agent:fixed"})()
    owned: list[tuple[Path, str]] = []
    monkeypatch.setattr(
        "bench.commands.eval_core.make_path_host_owned",
        lambda path, image: owned.append((path, image)),
    )
    monkeypatch.setattr(
        "bench.commands.eval_core._remove_ephemeral_agent_home",
        lambda *args, **kwargs: (_ for _ in ()).throw(PermissionError("sessions")),
    )

    _cleanup_ephemeral_agent_home(ed, stack)

    assert owned == [(ed.workspace, "agent:fixed")]
    assert "warning: could not remove ephemeral agent home" in ed.agent_log_path.read_text()


def test_workspace_ownership_failure_is_logged_without_skipping_secret_cleanup(
    monkeypatch, tmp_path: Path
) -> None:
    from bench.commands.eval_core import _cleanup_ephemeral_agent_home

    ed = compose_unit.EvalDir(root=tmp_path / "eval", eval_id="0" * 32)
    ed.workspace.mkdir(parents=True)
    (ed.root / "agent-home").mkdir(parents=True)
    stack = type("Stack", (), {"_agent_image": "agent:fixed"})()
    removed: list[bool] = []
    monkeypatch.setattr(
        "bench.commands.eval_core.make_path_host_owned",
        lambda *args: (_ for _ in ()).throw(PermissionError("workspace")),
    )
    monkeypatch.setattr(
        "bench.commands.eval_core._remove_ephemeral_agent_home",
        lambda *args, **kwargs: removed.append(True),
    )

    _cleanup_ephemeral_agent_home(ed, stack)

    assert removed == [True]
    assert "warning: could not return workspace to host" in ed.agent_log_path.read_text()

from pathlib import Path

import pytest

import bench.provenance as provenance_module
from bench.provenance import capture_image_identity, capture_provenance, tree_digest


def test_tree_digest_tracks_content_not_mtime_and_ignores_runtime_state(tmp_path: Path):
    (tmp_path / "task.yaml").write_text("id: task-0001\n")
    first = tree_digest(tmp_path)
    (tmp_path / "task.yaml").touch()
    assert tree_digest(tmp_path) == first

    cache = tmp_path / ".venv"
    cache.mkdir()
    (cache / "state").write_text("host-specific")
    assert tree_digest(tmp_path) == first

    (tmp_path / "task.yaml").write_text("id: task-0002\n")
    assert tree_digest(tmp_path) != first


def test_provenance_records_requested_and_effective_xhigh(tmp_path: Path, monkeypatch):
    (tmp_path / "task.yaml").write_text("id: task-0001\n")
    monkeypatch.setenv("IB_REASONING_EFFORT", "xhigh")
    monkeypatch.setenv("IB_REQUIRE_XHIGH_EFFORT", "1")

    provenance = capture_provenance(
        tmp_path, model="sonnet", provider="claude-code", mode="claude_code"
    )

    assert provenance["requested_reasoning_effort"] == "xhigh"
    assert provenance["effective_reasoning_effort"] == "xhigh"
    assert provenance["reasoning_effort"] == "xhigh"
    assert provenance["reasoning_effort_required_xhigh"] is True
    assert provenance["reasoning_effort_source"] == "claude --effort xhigh"


def test_required_xhigh_fails_closed(tmp_path: Path, monkeypatch):
    (tmp_path / "task.yaml").write_text("id: task-0001\n")
    monkeypatch.setenv("IB_REASONING_EFFORT", "high")
    monkeypatch.setenv("IB_REQUIRE_XHIGH_EFFORT", "1")

    with pytest.raises(RuntimeError, match="requires effective reasoning effort xhigh"):
        capture_provenance(tmp_path, model="sonnet", provider="claude-code", mode="claude_code")


def test_capture_image_identity_records_digest_id_and_agent_labels(monkeypatch):
    inspected = {
        "Id": "sha256:image-id",
        "RepoDigests": ["registry.example/agent@sha256:digest"],
        "Config": {
            "Labels": {
                "ib.agent.dockerfile_sha": "abcdef",
                "ib.agent.codex_version": "0.147.0",
                "unrelated": "ignored",
            }
        },
    }
    monkeypatch.setattr(
        provenance_module, "_run", lambda *args, **kwargs: __import__("json").dumps(inspected)
    )

    identity = capture_image_identity("registry.example/agent@sha256:digest")

    assert identity == {
        "reference": "registry.example/agent@sha256:digest",
        "image_id": "sha256:image-id",
        "repo_digests": ["registry.example/agent@sha256:digest"],
        "agent_labels": {
            "ib.agent.codex_version": "0.147.0",
            "ib.agent.dockerfile_sha": "abcdef",
        },
        "inspection": "resolved",
    }


def test_capture_image_identity_fails_explicitly_when_unavailable(monkeypatch):
    monkeypatch.setattr(provenance_module, "_run", lambda *args, **kwargs: None)
    identity = capture_image_identity("ib-agent:local")
    assert identity["inspection"] == "unavailable"
    assert identity["image_id"] is None

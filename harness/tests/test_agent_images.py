from bench import compose_unit


def test_build_agent_images_builds_unique_images_once(monkeypatch):
    calls = []

    def fake_ensure(*, tag, force, dockerfile):
        calls.append((tag, force, dockerfile.name))
        return tag

    monkeypatch.setattr(compose_unit, "ensure_agent_image", fake_ensure)

    result = compose_unit.build_agent_images(force=True)

    assert result == {
        "direct": compose_unit.AGENT_IMAGE,
        "claude-code": compose_unit.AGENT_IMAGE,
        "codex": compose_unit.CODEX_AGENT_IMAGE,
        "opencode": compose_unit.OPENCODE_AGENT_IMAGE,
    }
    assert [call[0] for call in calls] == [
        compose_unit.AGENT_IMAGE,
        compose_unit.CODEX_AGENT_IMAGE,
        compose_unit.OPENCODE_AGENT_IMAGE,
    ]
    assert all(call[1] is True for call in calls)


def test_build_agent_images_rejects_unknown_harness():
    try:
        compose_unit.build_agent_images(["unknown"])
    except compose_unit.ComposeError as exc:
        assert "unknown agent harness" in str(exc)
    else:
        raise AssertionError("unknown harness was accepted")

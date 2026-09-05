from __future__ import annotations

import subprocess

import pytest

import bench.images as images
from bench.images import ImageError


def _proc(rc: int, stderr: str = ""):
    return subprocess.CompletedProcess(["docker"], rc, stdout="", stderr=stderr)


def test_absent_image_reported_absent(monkeypatch) -> None:
    monkeypatch.setattr(images.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(
        images.subprocess,
        "run",
        lambda *a, **k: _proc(1, "Error: No such image: recruitos:nope"),
    )
    assert images._docker_image_exists("recruitos:nope") is False


def test_busy_daemon_retries_then_succeeds(monkeypatch) -> None:
    """Regression: a loaded daemon must not read as a missing image.

    `docker image inspect` exited non-zero seconds after a `compose down -v`
    while a manual inspect succeeded in ~31 ms; treating that as absence killed
    runs with "vendor image not found locally".
    """
    monkeypatch.setattr(images.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(images.time, "sleep", lambda _: None)
    calls = {"n": 0}

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] < 3:
            return _proc(1, "Cannot connect to the Docker daemon")
        return _proc(0)

    monkeypatch.setattr(images.subprocess, "run", flaky)
    assert images._docker_image_exists("recruitos:local") is True
    assert calls["n"] == 3


def test_persistent_daemon_fault_raises_not_absent(monkeypatch) -> None:
    """A daemon that never answers is an error, not a negative result."""
    monkeypatch.setattr(images.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(images.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        images.subprocess,
        "run",
        lambda *a, **k: _proc(1, "Cannot connect to the Docker daemon"),
    )
    with pytest.raises(ImageError, match="daemon fault"):
        images._docker_image_exists("recruitos:local")


def test_inspect_timeout_is_retried(monkeypatch) -> None:
    monkeypatch.setattr(images.shutil, "which", lambda _: "/usr/bin/docker")
    monkeypatch.setattr(images.time, "sleep", lambda _: None)
    calls = {"n": 0}

    def slow(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise subprocess.TimeoutExpired(["docker"], 30)
        return _proc(0)

    monkeypatch.setattr(images.subprocess, "run", slow)
    assert images._docker_image_exists("recruitos:local") is True

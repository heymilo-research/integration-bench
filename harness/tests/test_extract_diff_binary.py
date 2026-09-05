"""A binary artifact in the agent's workspace must not invalidate its whole patch.

Measured 2026-08-08 on the first live eval rollout (task-0001, haiku): the agent
tested its own work, which wrote test_output/canonical.db (SQLite). `git diff`
without --binary emits "Binary files a/x and b/x differ" — a placeholder with no
index data — and `git apply --check` then rejects the ENTIRE patch:

    error: cannot apply binary patch to 'test_output/canonical.db'
           without full index line

The agent's correct client.py and sync.py edits went ungraded and the rollout
scored 0.0, indistinguishable from a model that wrote nothing. Every task whose
agent runs the app before submitting is exposed, so unnoticed this would have
seeded a 200-rollout sweep with false zeros.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from bench.workspace import commit_baseline, extract_diff


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_SYSTEM": "/dev/null",
            "HOME": str(cwd),
        },
    )


@pytest.fixture
def agent_repo(tmp_path: Path) -> Path:
    """A baseline repo, then the edits a real rollout makes: a source change plus
    a generated binary test artifact."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "client.py").write_text("def fetch():\n    pass\n")
    commit_baseline(repo)

    # What the agent does: edit source, then run the app, which writes output.
    (repo / "src" / "client.py").write_text("def fetch():\n    return [1, 2, 3]\n")
    out = repo / "test_output"
    out.mkdir()
    (out / "candidates.json").write_text('[{"id": 1}]\n')
    # A SQLite-ish blob: NUL bytes are what make git treat it as binary.
    (out / "canonical.db").write_bytes(b"SQLite format 3\x00" + bytes(range(256)) * 4)
    return repo


def test_patch_with_a_binary_artifact_still_applies(agent_repo: Path, tmp_path: Path):
    patch = extract_diff(agent_repo)
    assert "GIT binary patch" in patch, "binary content must carry full index data"

    patch_file = tmp_path / "solution.patch"
    patch_file.write_text(patch)

    # Apply against a pristine checkout of the baseline, as grading does.
    pristine = tmp_path / "pristine"
    pristine.mkdir()
    (pristine / "src").mkdir()
    (pristine / "src" / "client.py").write_text("def fetch():\n    pass\n")
    commit_baseline(pristine)

    check = _git("apply", "--check", str(patch_file), cwd=pristine)
    assert check.returncode == 0, (
        f"git apply --check rejected the patch:\n{check.stdout}\n{check.stderr}"
    )


def test_the_source_change_survives_in_the_patch(agent_repo: Path):
    """The regression's real cost was losing the source edits, so assert them."""
    patch = extract_diff(agent_repo)
    assert "src/client.py" in patch
    assert "return [1, 2, 3]" in patch


def test_without_binary_flag_git_apply_would_reject(agent_repo: Path, tmp_path: Path):
    """Pins the mechanism: the old command really does produce an unappliable
    patch, so this test fails if someone drops --binary again."""
    _git("add", "-A", cwd=agent_repo)  # extract_diff does this; do it by hand here
    old = _git("diff", "--cached", "bench-baseline", cwd=agent_repo)
    patch_file = tmp_path / "old.patch"
    patch_file.write_text(old.stdout)
    assert "Binary files" in old.stdout

    pristine = tmp_path / "pristine2"
    pristine.mkdir()
    (pristine / "src").mkdir()
    (pristine / "src" / "client.py").write_text("def fetch():\n    pass\n")
    commit_baseline(pristine)

    check = _git("apply", "--check", str(patch_file), cwd=pristine)
    assert check.returncode != 0
    assert "without full index line" in (check.stderr + check.stdout)

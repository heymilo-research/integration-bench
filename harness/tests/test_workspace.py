from pathlib import Path

import pytest

from bench.workspace import (
    WorkspaceError,
    apply_patch,
    commit_baseline,
    extract_diff,
    prepare_pristine_repo,
    prepare_rollout_workspace,
)

SAMPLE_TASK = Path(__file__).parent / "fixtures" / "sample_task"


def test_prepare_rollout_workspace_copies_repo_problem_materials(tmp_path):
    workspace = tmp_path / "workspace"
    repo_dir = prepare_rollout_workspace(SAMPLE_TASK, workspace)
    assert repo_dir == workspace / "repo"
    assert (repo_dir / "main.py").is_file()
    assert (workspace / "PROBLEM.md").is_file()
    # sample_task has no materials/ dir; that's fine, it's optional.
    assert (repo_dir / ".git").is_dir()


def test_workspace_projection_copies_inputs_and_clears_cross_run_state(tmp_path):
    task = tmp_path / "task"
    (task / "repo").mkdir(parents=True)
    (task / "inputs").mkdir()
    (task / "repo" / "main.py").write_text("pass\n")
    (task / "inputs" / "records.json").write_text("[]\n")
    (task / "task.yaml").write_text(
        "contract:\n  participant:\n    workspace_allowlist:\n      - repo/**\n      - inputs/**\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "verifier-secret.txt").write_text("must disappear\n")

    prepare_rollout_workspace(task, workspace)

    assert (workspace / "inputs" / "records.json").read_text() == "[]\n"
    assert not (workspace / "verifier-secret.txt").exists()


def test_workspace_projection_rejects_evaluator_paths(tmp_path):
    task = tmp_path / "task"
    (task / "repo").mkdir(parents=True)
    (task / "repo" / "main.py").write_text("pass\n")
    (task / "verifier").mkdir()
    (task / "task.yaml").write_text(
        "contract:\n  participant:\n    workspace_allowlist:\n"
        "      - repo/**\n      - verifier/**\n",
        encoding="utf-8",
    )

    with pytest.raises(WorkspaceError, match="evaluator-only"):
        prepare_rollout_workspace(task, tmp_path / "workspace")


def test_commit_baseline_is_idempotent_and_tags(tmp_path):
    workspace = tmp_path / "workspace"
    repo_dir = prepare_rollout_workspace(SAMPLE_TASK, workspace)
    sha1 = commit_baseline(repo_dir)
    sha2 = commit_baseline(repo_dir)
    assert sha1 == sha2  # nothing changed, second commit is empty/no-op tag move


def test_extract_diff_empty_when_untouched(tmp_path):
    workspace = tmp_path / "workspace"
    repo_dir = prepare_rollout_workspace(SAMPLE_TASK, workspace)
    diff = extract_diff(repo_dir)
    assert diff.strip() == ""


def test_extract_diff_captures_agent_changes(tmp_path):
    workspace = tmp_path / "workspace"
    repo_dir = prepare_rollout_workspace(SAMPLE_TASK, workspace)
    (repo_dir / "main.py").write_text("def sync():\n    return 'synced'\n")
    diff = extract_diff(repo_dir)
    assert "synced" in diff
    assert "main.py" in diff


def test_extract_diff_captures_new_untracked_files(tmp_path):
    workspace = tmp_path / "workspace"
    repo_dir = prepare_rollout_workspace(SAMPLE_TASK, workspace)
    (repo_dir / "new_module.py").write_text("VALUE = 42\n")
    diff = extract_diff(repo_dir)
    assert "new_module.py" in diff
    assert "VALUE = 42" in diff


def test_apply_patch_empty_patch_succeeds(tmp_path):
    workspace = tmp_path / "workspace"
    repo_dir = prepare_pristine_repo(SAMPLE_TASK, workspace)
    empty_patch = tmp_path / "empty.patch"
    empty_patch.write_text("")
    ok, message = apply_patch(repo_dir, empty_patch)
    assert ok is True


def test_apply_patch_valid_patch_roundtrip(tmp_path):
    # Produce a real patch by editing one copy, then apply it to a separate
    # pristine copy and confirm the change lands.
    edited_workspace = tmp_path / "edited"
    edited_repo = prepare_pristine_repo(SAMPLE_TASK, edited_workspace)
    (edited_repo / "main.py").write_text("def sync():\n    return 'patched'\n")
    patch_text = extract_diff(edited_repo)
    patch_file = tmp_path / "solution.patch"
    patch_file.write_text(patch_text)

    pristine_workspace = tmp_path / "pristine"
    pristine_repo = prepare_pristine_repo(SAMPLE_TASK, pristine_workspace)
    ok, message = apply_patch(pristine_repo, patch_file)
    assert ok is True, message
    assert "patched" in (pristine_repo / "main.py").read_text()


def test_apply_patch_unapplyable_patch_is_not_a_crash(tmp_path):
    workspace = tmp_path / "workspace"
    repo_dir = prepare_pristine_repo(SAMPLE_TASK, workspace)
    garbage_patch = tmp_path / "garbage.patch"
    garbage_patch.write_text(
        "--- a/does_not_exist.py\n+++ b/does_not_exist.py\n@@ -1,1 +1,1 @@\n-old\n+new\n"
    )
    ok, message = apply_patch(repo_dir, garbage_patch)
    assert ok is False
    assert message  # non-empty diagnostic, no exception raised

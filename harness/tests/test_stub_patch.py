"""Unit tests for bench.commands.stub.stub_patch_text — the gauntlet's
do-nothing-but-clean-exit probe generator (validate.py gate 2b). Pure
filesystem + real git only; grade_once/docker is never invoked here."""

import shutil
import subprocess
from pathlib import Path

from bench.commands.stub import STUB_MAIN_BODY, stub_patch_text


def _make_task(
    tmp_path: Path,
    *,
    entry_command: str,
    pkg: str = "widgetsync",
    main_body: str = "print('do real work')\n",
    with_repo: bool = True,
    with_task_yaml: bool = True,
) -> Path:
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    if with_task_yaml:
        (task_dir / "task.yaml").write_text(
            f"id: task-9001\ncategory: build\nentry:\n  command: {entry_command}\n"
        )
    if with_repo:
        pkg_dir = task_dir / "repo" / "src" / pkg
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").write_text("")
        (pkg_dir / "__main__.py").write_text(main_body)
    return task_dir


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)


def test_produces_applyable_patch_that_installs_the_stub_body(tmp_path):
    task_dir = _make_task(tmp_path, entry_command='["python", "-m", "widgetsync"]')
    patch_text = stub_patch_text(task_dir)

    assert patch_text is not None
    assert "sys.exit(0)" in patch_text
    assert "widgetsync/__main__.py" in patch_text

    # Applies cleanly to a fresh, independently-committed copy of the repo.
    target = tmp_path / "apply-target"
    shutil.copytree(task_dir / "repo", target)
    _git("init", "-q", cwd=target)
    _git("config", "user.email", "a@b", cwd=target)
    _git("config", "user.name", "a", cwd=target)
    _git("add", "-A", cwd=target)
    _git("commit", "-q", "-m", "baseline", cwd=target)

    patch_file = tmp_path / "stub.patch"
    patch_file.write_text(patch_text)
    check = subprocess.run(
        ["git", "apply", "--check", str(patch_file)], cwd=target, capture_output=True, text=True
    )
    assert check.returncode == 0, check.stderr
    subprocess.run(["git", "apply", str(patch_file)], cwd=target, check=True)
    assert (target / "src" / "widgetsync" / "__main__.py").read_text() == STUB_MAIN_BODY


def test_allows_optional_trailing_subcommand(tmp_path):
    task_dir = _make_task(tmp_path, entry_command='["python", "-m", "widgetsync", "sync"]')
    assert stub_patch_text(task_dir) is not None


def test_none_when_entry_command_is_not_python_dash_m(tmp_path):
    task_dir = _make_task(tmp_path, entry_command='["bash", "run.sh"]')
    assert stub_patch_text(task_dir) is None


def test_direct_python_and_node_entries_get_clean_exit_stubs(tmp_path):
    for command, filename, body in (
        ('["python", "main.py"]', "main.py", "print('work')\n"),
        ('["node", "index.js"]', "index.js", "console.log('work');\n"),
    ):
        root = tmp_path / filename.replace(".", "-")
        root.mkdir()
        task = _make_task(root, entry_command=command)
        (task / "repo" / filename).write_text(body)
        assert stub_patch_text(task) is not None


def test_none_when_entry_command_has_no_package_arg(tmp_path):
    task_dir = _make_task(tmp_path, entry_command='["python", "-m"]')
    assert stub_patch_text(task_dir) is None


def test_none_when_no_main_for_the_named_package(tmp_path):
    # __main__.py exists, but under a different package name than entry names.
    task_dir = _make_task(tmp_path, entry_command='["python", "-m", "othername"]')
    assert stub_patch_text(task_dir) is None


def test_none_when_task_yaml_missing(tmp_path):
    task_dir = _make_task(
        tmp_path, entry_command='["python", "-m", "widgetsync"]', with_task_yaml=False
    )
    assert stub_patch_text(task_dir) is None


def test_none_when_repo_dir_missing(tmp_path):
    task_dir = _make_task(tmp_path, entry_command='["python", "-m", "widgetsync"]', with_repo=False)
    assert stub_patch_text(task_dir) is None


def test_none_when_diff_would_be_empty(tmp_path):
    # __main__.py already IS the stub body -> overwriting it produces no diff.
    task_dir = _make_task(
        tmp_path, entry_command='["python", "-m", "widgetsync"]', main_body=STUB_MAIN_BODY
    )
    assert stub_patch_text(task_dir) is None


def test_original_repo_directory_is_untouched(tmp_path):
    # stub_patch_text must work on a copy — the task's repo/ tree (which
    # solution.patch and the empty-patch probe also read) is never mutated.
    task_dir = _make_task(tmp_path, entry_command='["python", "-m", "widgetsync"]')
    main_path = task_dir / "repo" / "src" / "widgetsync" / "__main__.py"
    original = main_path.read_text()
    stub_patch_text(task_dir)
    assert main_path.read_text() == original
    assert not (task_dir / "repo" / ".git").exists()

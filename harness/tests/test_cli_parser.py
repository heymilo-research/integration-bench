import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import bench.cli as cli
from bench.cli import _build_parser


def test_parser_builds_all_subcommands():
    parser = _build_parser()
    sub_actions = [a for a in parser._subparsers._group_actions if hasattr(a, "choices")]
    choices = sub_actions[0].choices
    assert set(choices.keys()) == {
        "setup",
        "run",
        "grade",
        "validate",
        "validate-suite",
        "scoring-status",
        "run-all",
        "grade-all",
        "pull",
        "build-vendors",
        "build-agents",
        "release-check",
        "eval",
    }


def test_setup_and_release_check_parse_without_legacy_surfaces():
    parser = _build_parser()
    setup = parser.parse_args(["setup", "--skip-images"])
    assert setup.skip_images is True
    release = parser.parse_args(["release-check", "--require-promoted-images"])
    assert release.require_promoted_images is True


@pytest.mark.parametrize("harness", ["direct", "claude-code", "codex", "opencode"])
def test_eval_parses_all_harnesses(harness):
    parser = _build_parser()
    args = parser.parse_args(["eval", "--harness", harness, "--task", "tasks/public/task-0001"])
    assert args.harness == harness
    assert args.task == "tasks/public/task-0001"
    assert args.model is None
    assert args.variant is None
    assert args.max_turns is None
    assert args.keep is False


def test_eval_opencode_parses_model_and_variant():
    parser = _build_parser()
    args = parser.parse_args(
        [
            "eval",
            "--harness",
            "opencode",
            "--task",
            "tasks/public/task-0001",
            "--model",
            "moonshotai/kimi-k2.5",
            "--variant",
            "high",
        ]
    )
    assert args.model == "moonshotai/kimi-k2.5"
    assert args.variant == "high"


def test_build_agents_parses_selected_harnesses():
    parser = _build_parser()
    args = parser.parse_args(
        [
            "build-agents",
            "--harness",
            "codex",
            "--harness",
            "opencode",
            "--force",
        ]
    )
    assert args.harness == ["codex", "opencode"]
    assert args.force is True


@pytest.mark.parametrize(
    ("harness", "function_name", "extra_args"),
    [
        ("direct", "eval_once", []),
        ("claude-code", "eval_claude_code_once", []),
        ("codex", "eval_codex_once", []),
        ("opencode", "eval_opencode_once", ["--variant", "high"]),
    ],
)
def test_eval_dispatches_to_selected_harness(
    monkeypatch, tmp_path, capsys, harness, function_name, extra_args
):
    task = tmp_path / "task-0001"
    task.mkdir()
    (task / "task.yaml").write_text("id: task-0001\n")
    calls = []

    def fake_eval(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(
            eval_id="a" * 32,
            task_id="task-0001",
            model="test-model",
            provider="test-provider",
            turns=7,
            timed_out=False,
            stop_reason="done",
            resolved=True,
            reward=1.0,
            raw_score=100,
            task_score=1.0,
            check_coverage=1.0,
            missing_checks=[],
            failure_class="candidate_result",
            output_dir=Path("output") / ("a" * 32),
            patch_path=None,
            verdict_path=None,
            error=None,
        )

    monkeypatch.setattr(cli, function_name, fake_eval)
    argv = [
        "eval",
        "--harness",
        harness,
        "--task",
        str(task),
        "--model",
        "test-model",
        *extra_args,
    ]
    assert cli.main(argv) == 0
    assert len(calls) == 1
    summary = json.loads(capsys.readouterr().out)
    assert summary["harness"] == harness
    assert summary["turns"] == 7

    kwargs = calls[0][1]
    if harness == "direct":
        assert kwargs["max_turns"] == cli._DEFAULT_MAX_TURNS
    elif harness == "opencode":
        assert kwargs["variant"] == "high"


@pytest.mark.parametrize("harness", ["direct", "opencode"])
def test_eval_requires_model_for_harnesses_without_cli_default(harness, capsys):
    assert cli.main(["eval", "--harness", harness, "--task", "unused"]) == 2
    assert f"--model is required when --harness {harness}" in capsys.readouterr().err


def test_eval_rejects_lane_specific_flags_on_wrong_harness(capsys):
    assert (
        cli.main(
            [
                "eval",
                "--harness",
                "codex",
                "--task",
                "unused",
                "--variant",
                "high",
            ]
        )
        == 2
    )
    assert "--variant is only valid" in capsys.readouterr().err


def test_run_requires_task_and_agent():
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["run"])


def test_grade_parses_required_args():
    parser = _build_parser()
    args = parser.parse_args(["grade", "--task", "tasks/task-0001", "--patch", "sol.patch"])
    assert args.task == "tasks/task-0001"
    assert args.patch == "sol.patch"
    assert args.keep is False


def test_validate_default_runs_is_five():
    parser = _build_parser()
    args = parser.parse_args(["validate", "--task", "tasks/task-0001"])
    assert args.runs == 5


def test_validate_write_baseline_defaults_false():
    parser = _build_parser()
    args = parser.parse_args(["validate", "--task", "tasks/task-0001"])
    assert args.write_baseline is False


def test_validate_write_baseline_can_be_set():
    parser = _build_parser()
    args = parser.parse_args(["validate", "--task", "tasks/task-0001", "--write-baseline"])
    assert args.write_baseline is True


def test_keep_flag():
    parser = _build_parser()
    args = parser.parse_args(["run", "--task", "t", "--agent", "echo hi", "--keep"])
    assert args.keep is True

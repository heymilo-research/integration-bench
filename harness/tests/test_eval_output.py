"""Tests for eval_id / EvalDir helpers."""

from __future__ import annotations

import json

import pytest

from bench.eval_output import EvalDir, new_eval_id, validate_eval_id


def test_new_eval_id_is_32_hex():
    eid = new_eval_id()
    assert validate_eval_id(eid) == eid


def test_validate_eval_id_rejects_bad():
    with pytest.raises(ValueError):
        validate_eval_id("not-hex")


def test_eval_dir_records_immutable_attempt_and_logical_rollout(tmp_path):
    attempt = "a" * 32
    ed = EvalDir.create(
        output_root=tmp_path,
        eval_id=attempt,
        logical_rollout_id="task-0001:model:seed-1",
    )
    data = __import__("json").loads(ed.meta_path.read_text())
    assert data["attempt_id"] == attempt
    assert data["logical_rollout_id"] == "task-0001:model:seed-1"
    with pytest.raises(FileExistsError):
        EvalDir.create(output_root=tmp_path, eval_id=attempt)


def test_eval_dir_layout(tmp_path):
    ed = EvalDir.create(output_root=tmp_path)
    assert ed.workspace.is_dir()
    assert ed.meta_path.is_file()
    ed.append_transcript({"type": "test"})
    assert ed.transcript_path.is_file()
    ed.write_patch("diff --git a/x b/x\n")
    assert ed.patch_path.read_text().startswith("diff")


def test_runtime_secrets_are_redacted_from_all_retained_artifacts(tmp_path):
    ed = EvalDir.create(output_root=tmp_path)
    secret = "synthetic-runtime-secret-value-" + ("x" * 32)
    ed.register_runtime_secrets(secret)
    (ed.workspace / "repo").mkdir()
    leaked_file = ed.workspace / "repo" / "leak.txt"
    leaked_file.write_text(f"token={secret}\n")

    ed.append_agent_log(f"provider said {secret}")
    ed.append_transcript({"type": "error", "detail": secret})
    ed.write_patch_bytes(f"+TOKEN={secret}\n".encode())
    ed.scrub_runtime_secrets(ed.workspace)
    ed.write_meta(
        {
            "status": "done",
            "resolved": True,
            "reward": 1.0,
            "failure_class": "candidate_result",
        }
    )

    for path in (ed.agent_log_path, ed.transcript_path, ed.patch_path, leaked_file):
        assert secret.encode() not in path.read_bytes()
    meta = json.loads(ed.meta_path.read_text())
    assert meta["resolved"] is False
    assert meta["reward"] is None
    assert meta["failure_class"] == "candidate_runtime_failure"
    assert meta["secret_redaction"]["detected"] is True

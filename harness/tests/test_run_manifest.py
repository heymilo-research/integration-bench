from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

from bench.eval_output import EvalDir


ROOT = Path(__file__).resolve().parents[2]


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _provenance() -> dict:
    return {
        "schema": 1,
        "scorer_version": "task-score-v4-mandatory-gated",
        "task": "task-0001",
        "task_tree_sha256": "1" * 64,
        "task_git": {"commit": "abc1234", "dirty": False},
        "harness_tree_sha256": "2" * 64,
        "harness_git": {"commit": "abc1234", "dirty": False},
        "model": "test-model",
        "provider": "test-provider",
        "mode": "direct",
        "seed": "7",
        "effective_reasoning_effort": "xhigh",
        "requested_reasoning_effort": "xhigh",
        "tool_policy": "test-policy",
        "images_lock_sha256": _sha(ROOT / "images.lock.json"),
        "image_pins": {
            "vendor": {
                "vendor_id": "staffline",
                "resolved": "registry.example/staffline@sha256:" + "3" * 64,
            }
        },
        "agent_image": {
            "reference": "registry.example/agent@sha256:" + "4" * 64,
            "image_id": "sha256:" + "5" * 64,
            "repo_digests": [],
            "agent_labels": {},
            "inspection": "resolved",
        },
    }


def test_terminal_eval_writes_schema_valid_run_manifest(tmp_path: Path):
    ed = EvalDir.create(output_root=tmp_path, eval_id="a" * 32)
    ed.write_meta(
        {
            "status": "running",
            "task": "task-0001",
            "model": "test-model",
            "provider": "test-provider",
            "harness": "direct",
            "provenance": _provenance(),
        }
    )
    ed.write_patch("diff --git a/x b/x\n")
    ed.write_verdict_dict({"schema_version": 1, "task": "task-0001"})
    assert not ed.run_manifest_path.exists()

    ed.write_meta(
        {
            "status": "done",
            "resolved": True,
            "task_score": 100.0,
            "reward": 1.0,
            "scorer_version": "task-score-v4-mandatory-gated",
            "failure_class": "candidate_result",
            "elapsed_s": 1.25,
        }
    )

    manifest = json.loads(ed.run_manifest_path.read_text(encoding="utf-8"))
    schema = json.loads(
        (ROOT / "contracts/run-manifest/v1.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(manifest)
    assert manifest["run_id"] == "a" * 32
    assert manifest["task"] == "task-0001"
    assert manifest["seed"] == 7
    assert manifest["model"]["harness"] == "direct"
    assert manifest["catalog_hash"] == _sha(ROOT / "tasks/catalog.json")
    assert manifest["artifact_hashes"]["patch.diff"] == _sha(ed.patch_path)
    assert manifest["artifact_hashes"]["verdict.json"] == _sha(ed.verdict_path)
    assert "meta.json" not in manifest["artifact_hashes"]
    assert "run-manifest.json" not in manifest["artifact_hashes"]


def test_ungraded_provider_failure_still_has_manifest(tmp_path: Path):
    ed = EvalDir.create(output_root=tmp_path, eval_id="b" * 32)
    ed.write_patch("")
    ed.write_meta(
        {
            "status": "usage_limit",
            "task": "task-0001",
            "model": "test-model",
            "provider": "test-provider",
            "harness": "codex",
            "provenance": _provenance(),
            "failure_class": "provider_infrastructure_failure",
            "error": "usage limit",
        }
    )
    manifest = json.loads(ed.run_manifest_path.read_text(encoding="utf-8"))
    assert manifest["status"] == "usage_limit"
    assert manifest["failure_class"] == "provider_infrastructure_failure"
    assert manifest["outcome"]["task_score"] is None

from pathlib import Path

import pytest

from bench.config import ConfigError, TaskConfig, VendorMetadata, load_task_config

SAMPLE_TASK = Path(__file__).parent / "fixtures" / "sample_task"


def test_task_config_load():
    task = TaskConfig.load(SAMPLE_TASK)
    assert task.id == "task-9999"
    assert task.category == "build"
    assert task.vendor == "samplevendor"
    assert task.surfaces == ["polling"]
    assert task.tier == 1
    assert task.timeout_minutes == 5
    assert task.doc_profile == "true"
    assert task.scenarios == []
    assert task.entry == ["python", "-m", "sampleconnector"]
    assert task.outputs == {"files": []}


def test_vendor_metadata_load():
    task = TaskConfig.load(SAMPLE_TASK)
    vendor = task.vendors["samplevendor"]
    assert isinstance(vendor, VendorMetadata)
    assert vendor.name == "samplevendor"
    assert vendor.vendor_id == "samplevendor"
    assert vendor.product == "samplevendor"
    assert vendor.data_port == 8000
    assert vendor.checkpoint_env == "CHECKPOINT"
    assert vendor.checkpoint == 0
    assert vendor.credentials == {
        "SV_CLIENT_ID": "sample-client-id",
        "SV_CLIENT_SECRET": "sample-client-secret",
        "SV_WEBHOOK_SECRET": "sample-webhook-secret",
    }
    assert vendor.token_endpoint == "/oauth/token"
    assert vendor.token_ttl == 600


def test_load_task_config_ok():
    task = load_task_config(SAMPLE_TASK)
    assert task.vendor in task.vendors


def test_missing_vendor_reference_raises(tmp_path):
    (tmp_path / "task.yaml").write_text(
        "id: t\ntitle: x\ncategory: build\nvendor: missing\nsurfaces: []\n"
        "tier: 1\ntrack: both\ntimeout_minutes: 5\ndoc_profile: 'true'\n"
        "entry:\n  command: [echo]\noutputs:\n  files: []\n"
    )
    with pytest.raises(ConfigError):
        TaskConfig.load(tmp_path)


def test_missing_task_yaml_raises(tmp_path):
    with pytest.raises(ConfigError):
        TaskConfig.load(tmp_path)


def test_task_yaml_missing_required_field_raises(tmp_path):
    (tmp_path / "task.yaml").write_text("id: t\ntitle: x\n")
    with pytest.raises(ConfigError):
        TaskConfig.load(tmp_path)

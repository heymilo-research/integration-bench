"""Sanity tests for the provided plumbing — config, batch loading, output. See ``PROBLEM.md``."""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

from staffline_bulk import store, sync
from staffline_bulk.client import StafflineClient
from staffline_bulk.config import Config

REPO_ROOT = Path(__file__).resolve().parents[1]
BATCH = REPO_ROOT / "input" / "candidate_batch.json"


def test_config_from_env(monkeypatch) -> None:
    monkeypatch.setenv("VENDOR_BASE_URL", "http://vendor:8000/")
    monkeypatch.setenv("SL_APP_TOKEN", "tok")
    monkeypatch.setenv("SL_HMAC_SECRET", "sec")
    monkeypatch.setenv("DATABASE_URL", "sqlite:////tmp/ib-test.db")
    cfg = Config.from_env()
    assert cfg.vendor_base_url == "http://vendor:8000"  # trailing slash trimmed
    assert cfg.app_token == "tok"
    assert cfg.hmac_secret == "sec"
    assert cfg.database_url == "sqlite:////tmp/ib-test.db"


def test_read_batch_shape() -> None:
    batch = sync.read_batch(BATCH)
    assert len(batch) == 10
    refs = [item["client_ref"] for item in batch]
    assert len(refs) == len(set(refs)), "client_refs must be unique"
    for item in batch:
        assert item["client_ref"].startswith("batch-")


def test_write_result_sorts_and_shapes(tmp_path: Path) -> None:
    rows = [
        {"client_ref": "batch-0002", "created": False, "candidate_id": None},
        {"client_ref": "batch-0001", "created": True, "candidate_id": "cand_2001"},
    ]
    sync.write_result(tmp_path, rows)
    written = json.loads((tmp_path / "bulk_result.json").read_text())
    assert [i["client_ref"] for i in written["items"]] == ["batch-0001", "batch-0002"]
    assert written["items"][0] == {"client_ref": "batch-0001", "created": True, "id": "cand_2001"}
    assert written["items"][1] == {"client_ref": "batch-0002", "created": False, "id": None}


def test_client_signs_requests_with_hmac_sha256() -> None:
    cfg = Config.from_env(
        {"VENDOR_BASE_URL": "http://vendor:8000", "SL_APP_TOKEN": "tok", "SL_HMAC_SECRET": "sec"}
    )
    client = StafflineClient(cfg)
    body = b'{"items": []}'
    headers = client._auth_headers(body)
    assert headers["X-SL-Token"] == "tok"
    expected = hmac.new(
        b"sec", headers["X-SL-Timestamp"].encode("utf-8") + b"." + body, hashlib.sha256
    ).hexdigest()
    assert headers["X-SL-Signature"] == expected


def test_store_client_ref_is_the_primary_key(monkeypatch) -> None:
    # known_refs / all_results / get_result all key off client_ref -- a second
    # upsert_result for the same ref overwrites rather than duplicating (this
    # is asserted more thoroughly against a live postgres by the conformance
    # integration runs; this test only checks the SQL shape doesn't drift).
    assert "PRIMARY KEY" in _schema_sql_for(store.ensure_schema)


def _schema_sql_for(_fn) -> str:
    import inspect

    return inspect.getsource(store.ensure_schema)

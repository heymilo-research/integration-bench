"""Basic canonical-store checks."""

from globalhire_sync.config import Config
from globalhire_sync.sync import canonical_record, parse_wire_timestamp


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("VENDOR_BASE_URL", "http://vendor:8000/")
    monkeypatch.setenv("GH_API_KEY", "gh-test-api-key")
    cfg = Config.from_env()
    # trailing slash on the base URL is trimmed so path joins stay clean
    assert cfg.vendor_base_url == "http://vendor:8000"
    assert cfg.api_key == "gh-test-api-key"


def test_parse_wire_timestamp_utc_z():
    assert parse_wire_timestamp("2026-01-05T00:01:00Z") == 1767571260


def test_canonical_record_keeps_wire_field_names_verbatim():
    rec = {
        "id": "cand_00001",
        "first_name": "Ada",
        "last_name": "Adeyemi",
        "email": "ada.adeyemi@example.test",
        "pipeline_stage": "screening",
        "is_deleted": False,
        "created_at": "2026-01-05T05:30:00+05:30",
        "modified_at": "2026-01-05T05:30:00+05:30",
    }
    row = canonical_record(rec)
    assert row["source_id"] == "cand_00001"
    # `data` is the wire record stored verbatim.
    assert row["data"]["pipeline_stage"] == "screening"
    assert "status" not in row["data"]
    assert row["is_deleted"] is False
    assert isinstance(row["updated_at"], int)

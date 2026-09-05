"""Basic canonical-store checks."""

from talentforge_hooks.config import Config
from talentforge_hooks.sync import canonical_from_application, canonical_from_candidate


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("VENDOR_BASE_URL", "http://vendor:8000/")
    monkeypatch.setenv("DATABASE_URL", "sqlite:////tmp/ib-test.db")
    monkeypatch.setenv("TF_CLIENT_ID", "cid")
    monkeypatch.setenv("TF_WEBHOOK_SECRET", "shh")
    cfg = Config.from_env()
    # trailing slash on the base URL is trimmed so path joins stay clean
    assert cfg.vendor_base_url == "http://vendor:8000"
    assert cfg.database_url == "sqlite:////tmp/ib-test.db"
    assert cfg.client_id == "cid"
    assert cfg.webhook_secret == "shh"
    assert cfg.serve_port == 4000


def test_canonical_from_candidate_uses_epoch_millis_modified_at():
    rec = {
        "id": "cand_0042",
        "source_id": "cand_0042",
        "given_name": "Ada",
        "family_name": "Lovelace",
        "modified_at": 1773482430000,
        "is_deleted": False,
    }
    row = canonical_from_candidate(rec)
    assert row["source_id"] == "cand_0042"
    assert row["updated_at"] == 1773482430000
    assert isinstance(row["updated_at"], int)
    assert row["is_deleted"] is False


def test_canonical_from_application_uses_iso_modified_at():
    rec = {
        "id": "app_0005",
        "source_id": "app_0005",
        "stage": "interview",
        "modified_at": "2026-03-14T10:01:15Z",
        "is_deleted": False,
    }
    row = canonical_from_application(rec)
    assert row["source_id"] == "app_0005"
    assert row["updated_at"] == "2026-03-14T10:01:15Z"

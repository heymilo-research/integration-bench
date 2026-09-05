"""Basic canonical-store checks."""

from connector.config import Config


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

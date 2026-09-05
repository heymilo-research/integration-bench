"""Basic canonical-store checks."""

from globalhire_sync.config import Config


def test_config_from_env(monkeypatch):
    monkeypatch.setenv("VENDOR_BASE_URL", "http://vendor:8000/")
    monkeypatch.setenv("DATABASE_URL", "sqlite:////tmp/ib-test.db")
    monkeypatch.setenv("GH_API_KEY", "gh-test-api-key")
    cfg = Config.from_env()
    # trailing slash on the base URL is trimmed so path joins stay clean
    assert cfg.vendor_base_url == "http://vendor:8000"
    assert cfg.database_url == "sqlite:////tmp/ib-test.db"
    assert cfg.api_key == "gh-test-api-key"

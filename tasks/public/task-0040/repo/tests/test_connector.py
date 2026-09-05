"""Basic connector checks."""

from interviewly_writeback.config import Config
from interviewly_writeback.store import Store


def test_config_from_env():
    env = {
        "VENDOR_BASE_URL": "http://vendor:8000/",
        "IV_CLIENT_ID": "cid",
        "IV_WEBHOOK_SECRET": "shh",
        "INPUT_FILE": "/app/input/pending_reschedules.json",
    }
    cfg = Config.from_env(env)
    assert cfg.vendor_base_url == "http://vendor:8000"
    assert cfg.client_id == "cid"
    assert cfg.webhook_secret == "shh"
    assert str(cfg.input_file) == "/app/input/pending_reschedules.json"
    assert cfg.serve_port == 4000


def test_store_writebacks_roundtrip(tmp_path):
    store = Store(tmp_path)
    rows = store.load("writebacks")
    Store.upsert(
        rows,
        "resched-1",
        {"interview_id": "itv_0100", "status": "pending", "idempotency_key": "k1"},
        updated_at="00000001",
        is_deleted=False,
    )
    store.write("writebacks", rows)

    reloaded = store.load("writebacks")
    assert reloaded["resched-1"]["data"]["status"] == "pending"
    assert reloaded["resched-1"]["data"]["idempotency_key"] == "k1"

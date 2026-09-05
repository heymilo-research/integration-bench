"""Canonical-store tests. Run with: pytest"""

from interviewly_relay.config import Config
from interviewly_relay.store import Store


def test_config_from_env():
    env = {
        "VENDOR_BASE_URL": "http://vendor:8000/",
        "IV_CLIENT_ID": "cid",
        "IV_WEBHOOK_SECRET": "shh",
    }
    cfg = Config.from_env(env)
    # trailing slash on the base URL is trimmed so path joins stay clean
    assert cfg.vendor_base_url == "http://vendor:8000"
    assert cfg.client_id == "cid"
    assert cfg.webhook_secret == "shh"
    assert cfg.serve_port == 4000


def test_store_upsert_and_load(tmp_path):
    store = Store(tmp_path)
    rows = store.load("interviews")
    Store.upsert(rows, "itv_0001", {"candidate_name": "Ada"}, updated_at="2026-03-14T10:00:00Z", is_deleted=False)
    store.write("interviews", rows)

    reloaded = store.load("interviews")
    assert reloaded["itv_0001"]["data"]["candidate_name"] == "Ada"
    assert reloaded["itv_0001"]["is_deleted"] is False


def test_store_soft_delete_retains_row(tmp_path):
    store = Store(tmp_path)
    rows = store.load("interviews")
    Store.upsert(rows, "itv_0017", {"candidate_name": "Bea"}, updated_at="2026-03-14T10:00:00Z", is_deleted=False)
    Store.upsert(rows, "itv_0017", {"candidate_name": "Bea"}, updated_at="2026-03-14T11:02:00Z", is_deleted=True)
    store.write("interviews", rows)

    reloaded = store.load("interviews")
    assert "itv_0017" in reloaded
    assert reloaded["itv_0017"]["is_deleted"] is True


def test_event_journal_appends_per_record_in_order(tmp_path):
    store = Store(tmp_path)
    assert store.load_journal() == {}

    store.append_journal_entry("itv_0042", "evt_00001", "2026-03-14T11:01:00Z")
    store.append_journal_entry("fbk_9001", "evt_00004", "2026-03-14T11:04:00Z")
    store.append_journal_entry("itv_0042", "evt_00009", "2026-03-14T11:09:00Z")

    assert Store(tmp_path).load_journal() == {
        "itv_0042": [
            {"event_id": "evt_00001", "occurred_at": "2026-03-14T11:01:00Z"},
            {"event_id": "evt_00009", "occurred_at": "2026-03-14T11:09:00Z"},
        ],
        "fbk_9001": [
            {"event_id": "evt_00004", "occurred_at": "2026-03-14T11:04:00Z"},
        ],
    }


def test_state_roundtrip(tmp_path):
    store = Store(tmp_path)
    assert store.get_state("interviews.since") is None
    store.set_state("interviews.since", "2026-03-14T11:05:00Z")
    assert store.get_state("interviews.since") == "2026-03-14T11:05:00Z"

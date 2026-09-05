from talentloop_summit.config import Config
from talentloop_summit.sync import (
    canonical_from_application,
    canonical_from_candidate,
    canonical_from_job,
    canonical_from_note,
)


def test_config_defaults(monkeypatch):
    monkeypatch.delenv("VENDOR_BASE_URL", raising=False)
    cfg = Config.from_env()
    assert cfg.vendor_base_url == "http://localhost:8000"
    assert cfg.serve_port == 4000


def test_canonical_from_candidate():
    rec = {
        "id": "cand_0001", "source_id": "cand_0001", "given_name": "Ada",
        "family_name": "Curie", "email": "ada.curie@mail.test",
        "phone": "+1-555-1234", "pipeline_status": "screening",
        "created_at": "2026-01-01T00:00:00Z", "modified_at": "2026-01-02T00:00:00Z",
    }
    row = canonical_from_candidate(rec)
    assert row["source_id"] == "cand_0001"
    assert row["updated_at"] == "2026-01-02T00:00:00Z"
    assert "source_id" not in row["data"]


def test_canonical_from_job():
    rec = {
        "id": "job_0003", "source_id": "job_0003", "title": "Software Engineer",
        "status": "closed", "created_at": "2026-01-01T00:00:00Z",
        "modified_at": "2026-03-14T10:01:10Z",
    }
    row = canonical_from_job(rec)
    assert row["source_id"] == "job_0003"
    assert row["data"]["status"] == "closed"


def test_canonical_from_application():
    rec = {
        "id": "app_0001", "source_id": "app_0001", "candidate_id": "cand_0001",
        "job_id": "job_0001", "stage": "interview",
        "created_at": "2026-01-01T00:00:00Z", "modified_at": "2026-01-02T00:00:00Z",
    }
    row = canonical_from_application(rec)
    assert row["source_id"] == "app_0001"


def test_canonical_from_note():
    rec = {
        "id": "note_0004", "source_id": "note_0004", "candidate_id": "cand_0001",
        "body": "Updated after debrief.", "author": "recruiter@tl.test",
        "created_at": "2026-01-01T00:00:00Z", "modified_at": "2026-03-14T10:01:35Z",
    }
    row = canonical_from_note(rec)
    assert row["source_id"] == "note_0004"
    assert row["data"]["body"] == "Updated after debrief."

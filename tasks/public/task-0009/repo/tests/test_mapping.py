"""Mapping and normalization tests."""

from bullpen_migrate.mapping import normalize_application, normalize_candidate, normalize_job, to_wire_modified_since


def test_candidate_millis_to_utc():
    rec = {"id": "cand_0001", "source_id": "cand_0001", "created_at": 1772156691000,
           "modified_at": 1772156691000, "is_deleted": False}
    out = normalize_candidate(rec)
    assert out["modified_at"] == "2026-02-27T01:44:51Z"
    assert out["created_at"] == "2026-02-27T01:44:51Z"


def test_job_stays_iso():
    rec = {"id": "job_0001", "source_id": "job_0001", "created_at": "2026-02-17T21:29:02Z",
           "modified_at": "2026-02-17T21:29:02Z", "is_deleted": False}
    out = normalize_job(rec)
    assert out["modified_at"] == "2026-02-17T21:29:02Z"


def test_application_bucket_to_stage():
    rec = {"id": "app_0001", "source_id": "app_0001", "candidate_id": "cand_0001",
           "job_id": "job_0001", "bucket": "interview", "created_at": "2026-02-17T21:29:02Z",
           "modified_at": "2026-02-17T21:29:02Z", "is_deleted": False}
    out = normalize_application(rec)
    assert out["stage"] == "interview"
    assert "bucket" not in out


def test_watermark_roundtrips_to_millis_for_candidates_only():
    assert to_wire_modified_since("candidates", "2026-02-27T01:44:51Z") == "1772156691000"
    assert to_wire_modified_since("jobs", "2026-02-17T21:29:02Z") == "2026-02-17T21:29:02Z"

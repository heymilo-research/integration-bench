from staffline_to_bullpen_migrate.bullpen_mapping import normalize_application, normalize_candidate


def test_candidate_millis_to_utc():
    rec = {"id": "cand_0001", "source_id": "cand_0001", "created_at": 1775454839000,
           "modified_at": 1775454839000, "is_deleted": False}
    out = normalize_candidate(rec)
    assert out["modified_at"] == "2026-04-06T05:53:59Z"


def test_application_bucket_renamed_to_stage():
    rec = {"id": "app_0001", "source_id": "app_0001", "bucket": "interview",
           "created_at": "2026-02-27T01:44:51Z", "modified_at": "2026-02-27T01:44:51Z",
           "is_deleted": False}
    out = normalize_application(rec)
    assert out["stage"] == "interview"
    assert "bucket" not in out

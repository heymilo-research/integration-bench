"""Regression tests for verifier-contract defects found by the 50-task audit."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]


def _load(relative: str):
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(path.stem + "_contract_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Public-task scenarios deliberately use the common top-level module name
    # ``_scenario_util``.  Do not let one task's helper leak into the next
    # dynamically loaded scenario in this multi-task regression file.
    previous_util = sys.modules.pop("_scenario_util", None)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop("_scenario_util", None)
        if previous_util is not None:
            sys.modules["_scenario_util"] = previous_util
    return module


class _Recorder:
    def __init__(self):
        self.rows = {}

    def check(self, name, ok, detail, **_scoring):
        self.rows[name] = (bool(ok), detail)


def test_0001_include_stage_presence_accepts_equivalent_vendor_values():
    scenario = _load("tasks/public/task-0001/verifier/scenarios/initial_sync.py")
    assert scenario._carries_include_stage({"query": {"include_stage": "1"}})
    assert scenario._carries_include_stage({"query": {"include_stage": "true"}})
    assert not scenario._carries_include_stage({"query": {}})


def test_0012_webhook_credit_requires_pre_poll_state_not_ack_alone():
    scenario = _load("tasks/public/task-0012/verifier/scenarios/webhook_and_poll_freshness.py")
    untouched = {
        "candidate": [
            {"source_id": "cand_0007", "is_deleted": False, "data": {}},
            {
                "source_id": "cand_0055",
                "is_deleted": False,
                "data": {"pipeline_status": "screening"},
            },
        ],
        "application": [{"source_id": "app_0009", "is_deleted": False, "data": {}}],
    }
    ok, detail = scenario._webhook_target_state(untouched)
    assert ok is False
    assert "cand_0007.is_deleted" in detail

    applied = {
        "candidate": [
            {"source_id": "cand_0007", "is_deleted": True, "data": {}},
            {
                "source_id": "cand_0055",
                "is_deleted": False,
                "data": {"pipeline_status": "placed"},
            },
        ],
        "application": [{"source_id": "app_0009", "is_deleted": True, "data": {}}],
    }
    assert scenario._webhook_target_state(applied)[0] is True


def test_0014_required_result_projection_allows_diagnostic_fields():
    scenario = _load("tasks/public/task-0014/verifier/scenarios/correction_backlog.py")
    expected = {"candidate_id": "cand_0019", "ok": True, "stage": "rejected"}
    enriched = {
        **expected,
        "action": "corrected",
        "event_id": "evt_90001",
        "error": None,
    }
    assert scenario._contains_required_fields(enriched, expected)
    assert not scenario._contains_required_fields({**enriched, "stage": "screening"}, expected)


def test_0011_tamper_presence_cannot_bank_failed_asynchronous_delivery():
    source = (ROOT / "tasks/public/task-0011/verifier/scenarios/tamper_rejection.py").read_text(
        encoding="utf-8"
    )
    assert (
        'ctx.check("tampered_delivery_present",\n        len(tampered) >= 1 and drained,' in source
    )


def test_0012_malformed_writeback_sections_are_scored_instead_of_crashing():
    util = _load("tasks/public/task-0012/verifier/scenarios/_scenario_util.py")
    fixture = {
        "events": [
            {
                "client_ref": "bad-note",
                "ok": False,
                "error": {"status": 422, "field_errors": {"body": ["is required"]}},
            }
        ]
    }
    malformed = {
        "events": [
            {
                "client_ref": "bad-note",
                "ok": False,
                "error": 'HTTP 422: {"errors":{"body":["is required"]}}',
            }
        ]
    }

    assert util.writeback_event_diff(malformed, fixture) == [
        'bad-note.error: got=\'HTTP 422: {"errors":{"body":["is required"]}}\' want an object'
    ]
    assert util.writeback_record_ids(
        {"events": [{"client_ref": "bad-note", "ok": True, "record": "not-an-object"}]}
    ) == {"bad-note": None}
    assert util.store_row_diff(
        [{"source_id": "candidate-1", "data": "not-an-object"}],
        [{"source_id": "candidate-1", "data": {"status": "active"}}],
    ) == ["candidate-1.data: got='not-an-object' want an object"]


def test_0028_fault_offset_is_derived_from_contract_valid_page_size_50():
    util = _load("tasks/public/task-0028/verifier/scenarios/_scenario_util.py")
    ctx = _Recorder()
    log = [
        {
            "method": "GET",
            "path": "/v1/workers",
            "query": {"offset": "0", "limit": "50"},
            "status": 200,
            "ts": 1.0,
        },
        {
            "method": "GET",
            "path": "/v1/workers",
            "query": {"offset": "50", "limit": "50"},
            "status": 500,
            "ts": 2.0,
        },
        {
            "method": "GET",
            "path": "/v1/workers",
            "query": {"offset": "50", "limit": "50"},
            "status": 200,
            "ts": 3.0,
        },
    ]
    util.check_log_forensics(ctx, log)
    assert ctx.rows["resume_not_restart"][0] is True
    assert "fault_offset=50" in ctx.rows["resume_not_restart"][1]


def test_0035_data_projection_allows_extra_raw_fields_but_requires_business_values():
    scenario = _load("tasks/public/task-0035/verifier/scenarios/initial_backfill.py")
    want = [
        {
            "source_id": "rpt_1",
            "data": {"status": "finished", "completed_at": 123},
            "updated_at": 124,
            "is_deleted": False,
        }
    ]
    got = [
        {
            "source_id": "rpt_1",
            "data": {
                "status": "finished",
                "completed_at": 123,
                "id": "rpt_1",
                "updated_at": 124,
                "is_deleted": False,
                "finished_at": 123,
            },
            "updated_at": 124,
            "is_deleted": False,
        }
    ]
    assert scenario._row_diff(got, want) == []
    got[0]["data"]["completed_at"] = 999
    assert scenario._row_diff(got, want) == [
        {"source_id": "rpt_1", "field": "data.completed_at", "want": 123, "got": 999}
    ]


def test_0040_retry_accepts_safe_local_skip_or_same_key_resend():
    scenario = _load("tasks/public/task-0040/verifier/scenarios/idempotent_retry.py")
    first = {"idempotency_key": "reschedule:resched-1"}
    assert scenario._safe_retry_policy([first]) is True
    assert scenario._safe_retry_policy([first, dict(first)]) is True
    assert (
        scenario._safe_retry_policy([first, {"idempotency_key": "reschedule:fresh-key"}]) is False
    )
    assert scenario._safe_retry_policy([]) is False


def test_0045_legacy_tombstone_accepts_equivalent_payload_and_iso_time():
    scenario = _load("tasks/public/task-0045/verifier/scenarios/cutover_backfill_to_bullpen.py")
    want = [
        {
            "source_id": "legacy:candidate:cand_0017",
            "data": {"staffline_id": "cand_0017"},
            "is_deleted": True,
            "updated_at": 1546819125000,
        }
    ]
    got = [
        {
            "source_id": "legacy:candidate:cand_0017",
            "data": {},
            "is_deleted": True,
            "updated_at": "2019-01-06T23:58:45Z",
        }
    ]
    assert scenario._row_diff(got, want) == []
    got[0]["updated_at"] = "2019-01-08T00:25:25Z"
    assert any(d["field"] == "updated_at" for d in scenario._row_diff(got, want))


def test_0045_writeback_contract_does_not_require_output_idempotency_key():
    # The fixture may retain the historical field for old-result comparison,
    # but the verifier's contract loop must be limited to the five published
    # fields; request-log assertions own idempotency evidence.
    source = (
        ROOT / "tasks/public/task-0045/verifier/scenarios/writeback_lands_on_new_vendor.py"
    ).read_text()
    assert 'for field in ("op", "candidate_id", "ok", "id", "err")' in source


def test_standalone_grading_uses_the_vendor_readiness_contract():
    source = (ROOT / "harness/bench/commands/grading_core.py").read_text(encoding="utf-8")
    assert 'wait_for_http(f"{url}/_ready", timeout_s=startup_timeout_s)' in source
    assert "use_compose_unit" not in source

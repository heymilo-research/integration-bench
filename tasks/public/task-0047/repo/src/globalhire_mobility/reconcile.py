from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from globalhire_mobility.client import GlobalHireClient
from globalhire_mobility.import_file import LogicalAction


STAGES = ("sourced", "screening", "submitted", "interview", "offer", "placed")


@dataclass
class CaseResult:
    case_ref: str
    source_line: int
    duplicate_count: int
    candidate_id: str
    placement_id: str
    agency_id: str
    requested_stage: str
    current_stage: str
    outcome: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RecordCache:
    def __init__(self, client: GlobalHireClient):
        self.client = client
        self.candidates: dict[str, dict[str, Any] | None] = {}
        self.placements: dict[str, dict[str, Any] | None] = {}
        self.agencies: dict[str, dict[str, Any] | None] = {}

    def candidate(self, record_id: str) -> dict[str, Any] | None:
        if record_id not in self.candidates:
            self.candidates[record_id] = self.client.candidate(record_id)
        return self.candidates[record_id]

    def placement(self, record_id: str) -> dict[str, Any] | None:
        if record_id not in self.placements:
            self.placements[record_id] = self.client.placement(record_id)
        return self.placements[record_id]

    def agency(self, record_id: str) -> dict[str, Any] | None:
        if record_id not in self.agencies:
            self.agencies[record_id] = self.client.agency(record_id)
        return self.agencies[record_id]


def _result(
    logical: LogicalAction,
    current_stage: str,
    outcome: str,
    reason: str,
) -> CaseResult:
    action = logical.action
    return CaseResult(
        case_ref=action.case_ref,
        source_line=action.source_line,
        duplicate_count=logical.duplicate_count,
        candidate_id=action.candidate_id,
        placement_id=action.placement_id,
        agency_id=action.agency_id,
        requested_stage=action.requested_stage,
        current_stage=current_stage,
        outcome=outcome,
        reason=reason,
    )


def reconcile(
    client: GlobalHireClient, logical: list[LogicalAction]
) -> list[CaseResult]:
    records = RecordCache(client)
    results: list[CaseResult] = []
    eligible: list[tuple[CaseResult, dict[str, str]]] = []

    for item in logical:
        action = item.action
        if not all(
            (
                action.case_ref,
                action.candidate_id,
                action.placement_id,
                action.agency_id,
                action.requested_stage,
            )
        ):
            results.append(_result(item, "", "rejected", "invalid_input"))
            continue

        candidate = records.candidate(action.candidate_id)
        if candidate is None:
            results.append(_result(item, "", "rejected", "candidate_not_found"))
            continue
        current = str(candidate.get("status") or candidate.get("pipeline_stage") or "")
        visible_current = current if current in STAGES else ""
        if candidate.get("is_deleted"):
            results.append(_result(item, visible_current, "rejected", "candidate_deleted"))
            continue

        placement = records.placement(action.placement_id)
        if placement is None:
            results.append(_result(item, visible_current, "rejected", "placement_not_found"))
            continue
        if placement.get("is_deleted"):
            results.append(_result(item, visible_current, "rejected", "placement_deleted"))
            continue
        if placement.get("placement_state") != "active":
            results.append(_result(item, visible_current, "rejected", "placement_not_active"))
            continue
        if placement.get("candidate_id") != action.candidate_id:
            results.append(
                _result(item, visible_current, "rejected", "placement_candidate_mismatch")
            )
            continue
        if placement.get("agency_id") != action.agency_id:
            results.append(
                _result(item, visible_current, "rejected", "placement_agency_mismatch")
            )
            continue

        agency = records.agency(action.agency_id)
        if agency is None:
            results.append(_result(item, visible_current, "rejected", "agency_not_found"))
            continue
        if agency.get("is_deleted"):
            results.append(_result(item, visible_current, "rejected", "agency_deleted"))
            continue
        if current not in STAGES:
            results.append(_result(item, "", "rejected", "invalid_current_stage"))
            continue
        if action.requested_stage not in STAGES:
            results.append(
                _result(item, current, "rejected", "invalid_requested_stage")
            )
            continue
        if STAGES.index(action.requested_stage) < STAGES.index(current):
            results.append(_result(item, current, "rejected", "stage_regression"))
            continue
        if action.requested_stage == current:
            results.append(_result(item, current, "unchanged", "already_at_stage"))
            continue

        planned = _result(item, current, "updated", "stage_applied")
        results.append(planned)
        eligible.append(
            (
                planned,
                {
                    "candidate_id": action.candidate_id,
                    "pipeline_stage": action.requested_stage,
                },
            )
        )

    client.update_stages([update for _result_row, update in eligible])
    return results

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Action:
    source_line: int
    case_ref: str
    candidate_id: str
    placement_id: str
    agency_id: str
    requested_stage: str

    def values(self) -> tuple[str, str, str, str]:
        return (
            self.candidate_id,
            self.placement_id,
            self.agency_id,
            self.requested_stage,
        )


@dataclass(frozen=True)
class LogicalAction:
    action: Action
    duplicate_count: int


def read_actions(path: Path) -> list[Action]:
    actions: list[Action] = []
    with Path(path).open(encoding="utf-8", newline="") as handle:
        for line_number, raw in enumerate(csv.DictReader(handle), start=2):
            row = {key: (value or "").strip() for key, value in raw.items()}
            actions.append(
                Action(
                    source_line=line_number,
                    case_ref=row.get("case_ref", ""),
                    candidate_id=row.get("candidate_id", ""),
                    placement_id=row.get("placement_id", ""),
                    agency_id=row.get("agency_id", ""),
                    requested_stage=row.get("requested_stage", ""),
                )
            )
    return actions


def logical_actions(actions: list[Action]) -> list[LogicalAction]:
    first: dict[str, Action] = {}
    counts: dict[str, int] = {}
    for action in actions:
        first.setdefault(action.case_ref, action)
        counts[action.case_ref] = counts.get(action.case_ref, 0) + 1
    return [
        LogicalAction(action=action, duplicate_count=counts[case_ref])
        for case_ref, action in first.items()
    ]

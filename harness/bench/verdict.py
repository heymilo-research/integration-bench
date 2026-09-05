"""Verdict schema and aggregation (docs/SPEC.md §5, §6; conduct-rules.md).

Verdict JSON shape (exact keys required by the spec prompt). The canonical
machine-readable contract is contracts/verdict/v1.json:

    {
      "schema_version": 1,
      "task": str,
      "run_id": str,
      "resolved": bool,
      "l1": [ {"name", "ok", "detail"}, ... ],
      "l2": {
        "hard": [ {"name", "ok", "detail"}, ... ],
        "soft": {"violations": int, "checks": int, "score": float, "results": [...]}
      },
      "l3": [ {"name", "ok", "detail"}, ... ],
      "error": null | str
    }

`resolved` = all L1 ok AND no hard violations (all l2.hard ok) AND all L3 ok
(vacuously true if no L3 checks were recorded).

`l2.soft.results` is an additive field (per-check detail) beyond the minimal
{violations, checks, score} shape the spec calls out — kept because it costs
nothing and materially helps debugging; consumers that only read
violations/checks/score are unaffected.
"""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any


#: Legal per-test values. Deliberately closed — see TODO "Keep the value set at
#: {-1,0,1,2} — do not add more levels."
VALUES = (-1, 0, 1, 2)


@dataclasses.dataclass
class Check:
    """One recorded test result, carrying its own scoring.

    Scoring lives at the ``ctx.check(...)`` call site rather than in a sidecar or
    a role table: the author reads the test and states what passing and failing
    are worth. ``value`` is then just a lookup, not an inference.

    The three scoring fields are ADDITIVE and defaulted, so a schema-1 verdict on
    disk still loads and a scenario that has not been migrated still records.
    Defaults describe an ordinary correctness check: passing earns 1, failing
    earns nothing, and it does not gate ``Solved``.
    """

    name: str
    ok: bool
    detail: str = ""
    pass_value: int = 1
    fail_value: int = 0
    mandatory: bool = False

    def __post_init__(self) -> None:
        for field, v in (("pass_value", self.pass_value), ("fail_value", self.fail_value)):
            if v not in VALUES:
                raise ValueError(f"check {self.name!r}: {field}={v!r} is not one of {VALUES}")

    @property
    def value(self) -> int:
        """What this result contributes to the task's raw score."""
        return self.pass_value if self.ok else self.fail_value

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "pass_value": self.pass_value,
            "fail_value": self.fail_value,
            "mandatory": self.mandatory,
            # Denormalised on purpose: a captured verdict stays re-scorable
            # without needing the scenario source that produced it.
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Check":
        return cls(
            name=data["name"],
            ok=bool(data["ok"]),
            detail=data.get("detail", ""),
            pass_value=int(data.get("pass_value", 1)),
            fail_value=int(data.get("fail_value", 0)),
            mandatory=bool(data.get("mandatory", False)),
        )


@dataclasses.dataclass
class Verdict:
    task: str
    run_id: str
    l1: list[Check] = dataclasses.field(default_factory=list)
    hard: list[Check] = dataclasses.field(default_factory=list)
    soft: list[Check] = dataclasses.field(default_factory=list)
    l3: list[Check] = dataclasses.field(default_factory=list)
    error: str | None = None

    @property
    def resolved(self) -> bool:
        if self.error:
            return False
        l1_ok = all(c.ok for c in self.l1)
        hard_ok = all(c.ok for c in self.hard)
        l3_ok = all(c.ok for c in self.l3)
        return l1_ok and hard_ok and l3_ok

    def soft_score(self) -> float:
        checks = len(self.soft)
        if checks == 0:
            return 1.0
        violations = sum(1 for c in self.soft if not c.ok)
        return 1 - violations / checks

    def to_dict(self) -> dict[str, Any]:
        violations = sum(1 for c in self.soft if not c.ok)
        # schema_version is the FIRST key so consumers can branch on it before
        # reading anything else (contracts/verdict/v1.json). Within major
        # version 1 only additive changes are permitted, so v1 readers never
        # need to inspect it beyond confirming the major matches.
        return {
            "schema_version": 1,
            "task": self.task,
            "run_id": self.run_id,
            "resolved": self.resolved,
            "l1": [c.to_dict() for c in self.l1],
            "l2": {
                "hard": [c.to_dict() for c in self.hard],
                "soft": {
                    "violations": violations,
                    "checks": len(self.soft),
                    "score": self.soft_score(),
                    "results": [c.to_dict() for c in self.soft],
                },
            },
            "l3": [c.to_dict() for c in self.l3],
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Verdict":
        l2 = data.get("l2", {}) or {}
        soft = l2.get("soft", {}) or {}
        return cls(
            task=data["task"],
            run_id=data["run_id"],
            l1=[Check.from_dict(c) for c in data.get("l1", [])],
            hard=[Check.from_dict(c) for c in l2.get("hard", [])],
            soft=[Check.from_dict(c) for c in soft.get("results", [])],
            l3=[Check.from_dict(c) for c in data.get("l3", [])],
            error=data.get("error"),
        )

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False)

    def write(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.to_json() + "\n", encoding="utf-8")

    @classmethod
    def error_verdict(cls, task: str, run_id: str, error: str) -> "Verdict":
        return cls(task=task, run_id=run_id, error=error)


def _strip_diagnostics(value: Any) -> Any:
    """Drop run_id and free-text `detail` strings from a verdict dict.

    The verdict's semantic contract is check names, ok flags, scores, and
    resolved/error state. Details are diagnostics and may legitimately embed
    wall-clock-dependent values (e.g. token-mint counts under a short TTL), so
    they are excluded from flake-gate equality.
    """
    if isinstance(value, dict):
        return {k: _strip_diagnostics(v) for k, v in value.items() if k not in ("run_id", "detail")}
    if isinstance(value, list):
        return [_strip_diagnostics(v) for v in value]
    return value


def verdicts_equal_ignoring_run_id(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Structural equality used by the flake gate (conduct-rules.md gate 3):
    identical verdicts across repeated runs, ignoring run_id and diagnostic
    detail strings."""
    return _strip_diagnostics(a) == _strip_diagnostics(b)


def verdict_semantic_diff(a: dict[str, Any], b: dict[str, Any], *, limit: int = 20) -> list[str]:
    """Describe semantic verdict drift using stable JSON-style paths."""
    left = _strip_diagnostics(a)
    right = _strip_diagnostics(b)
    differences: list[str] = []

    def walk(x: Any, y: Any, path: str) -> None:
        if len(differences) >= limit:
            return
        if type(x) is not type(y):
            differences.append(f"{path}: type {type(x).__name__} != {type(y).__name__}")
            return
        if isinstance(x, dict):
            for key in sorted(set(x) | set(y)):
                child = f"{path}.{key}"
                if key not in x:
                    differences.append(f"{child}: missing in first verdict")
                elif key not in y:
                    differences.append(f"{child}: missing in repeated verdict")
                else:
                    walk(x[key], y[key], child)
                if len(differences) >= limit:
                    return
            return
        if isinstance(x, list):
            if len(x) != len(y):
                differences.append(f"{path}: length {len(x)} != {len(y)}")
            for index, (x_item, y_item) in enumerate(zip(x, y)):
                walk(x_item, y_item, f"{path}[{index}]")
                if len(differences) >= limit:
                    return
            return
        if x != y:
            differences.append(f"{path}: {x!r} != {y!r}")

    walk(left, right, "$")
    return differences

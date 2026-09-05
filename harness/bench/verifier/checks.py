"""Check-recording helpers shared by VerifierContext. Never raise — scenarios
call these to record a result and keep driving; the harness aggregates
whatever was recorded into the final verdict, regardless of whether the
scenario itself later raises.

Scoring model (TODO "Decided — scoring, 2026-08-07"): every test states what it
is worth at the call site.

    ctx.check(
        "pkt_0003_conflict_seen_and_edit_accepted",
        ok,
        pass_value=2,      # important behaviour
        fail_value=0,
        mandatory=True,    # a real solve must have this
        detail=detail,
    )

``check_l1`` / ``check_hard`` / ``check_soft`` / ``check_l3`` remain as thin
shims so the 50-task tree keeps working during migration. They are NOT the
target state: the bucket names carry no scoring meaning any more, they only pick
a default. Migration is per task, and `bench validate-suite` reports how much of
the tree is still on the legacy calls.
"""

from __future__ import annotations

from bench.verdict import Check

#: Per-bucket defaults for the legacy entry points, chosen to match the
#: authoring guide:
#:   l1/l3  — new implementation behaviour: pass +1, fail 0
#:   hard   — conduct gate; the starter already satisfies it, so passing earns
#:            nothing and regressing costs (preserve-style: 0 / -1)
#:   soft   — cosmetic/advisory: scores nothing either way
_LEGACY_DEFAULTS = {
    "l1": (1, 0),
    "l3": (1, 0),
    "hard": (0, -1),
    "soft": (0, 0),
}


class CheckRecorder:
    def __init__(self) -> None:
        self.l1: list[Check] = []
        self.hard: list[Check] = []
        self.soft: list[Check] = []
        self.l3: list[Check] = []
        #: Names recorded through a legacy bucket method. Migration progress is
        #: measurable rather than guessed.
        self.legacy_calls: list[str] = []

    # -- the target API ----------------------------------------------------

    def check(
        self,
        name: str,
        ok: bool,
        detail: str = "",
        *,
        pass_value: int = 1,
        fail_value: int = 0,
        mandatory: bool = False,
        bucket: str = "l1",
    ) -> None:
        """Record a test result and what it is worth.

        ``bucket`` survives only as a REPORTING label — which dimension failed —
        and has no effect on the score. Everything that decides the score is on
        this call.
        """
        target = getattr(self, bucket, None)
        if target is None:
            raise ValueError(f"unknown bucket {bucket!r} for check {name!r}")
        target.append(
            Check(
                name=name,
                ok=ok,
                detail=detail,
                pass_value=pass_value,
                fail_value=fail_value,
                mandatory=mandatory,
            )
        )

    # -- legacy shims, kept until every task is migrated -------------------

    def _legacy(self, bucket: str, name: str, ok: bool, detail: str) -> None:
        pass_value, fail_value = _LEGACY_DEFAULTS[bucket]
        self.legacy_calls.append(name)
        self.check(
            name,
            ok,
            pass_value=pass_value,
            fail_value=fail_value,
            mandatory=False,
            detail=detail,
            bucket=bucket,
        )

    def check_l1(self, name: str, ok: bool, detail: str = "") -> None:
        self._legacy("l1", name, ok, detail)

    def check_hard(self, name: str, ok: bool, detail: str = "") -> None:
        self._legacy("hard", name, ok, detail)

    def check_soft(self, name: str, ok: bool, detail: str = "") -> None:
        self._legacy("soft", name, ok, detail)

    def check_l3(self, name: str, ok: bool, detail: str = "") -> None:
        self._legacy("l3", name, ok, detail)

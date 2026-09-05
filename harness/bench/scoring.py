"""Per-test scoring: Task Score, Solved, and the two suite metrics.

Design record: TODO.md "Decided — scoring (2026-08-07)". This replaces the
floor-and-ceiling normalisation (`reward_from_verdict`) and the bucket-weighted
average (`score_v2`). Both of those tried to infer, from probe outcomes or from
a bucket label, how much a check ought to be worth. This asks the author.

    raw        = sum(check.value)                # value = pass_value or fail_value
    max_score  = sum of positive values ON GOLD
    Task Score = 0 if a mandatory check fails; otherwise
                 100 * clamp(raw, 0, max_score) / max_score
    Solved     = every mandatory check has ok=True

`max_score` comes from GOLD, not from the run being scored, which is what makes
the denominator fixed: a run that crashes half way cannot shrink the set it is
measured against. Unreached checks are scored at their authored `fail_value`.

Why the gold denominator is not simply "sum of all pass_values": gold is allowed
to miss a cosmetic check, and negative-valued preserve checks must not inflate
the ceiling. Summing the POSITIVE values gold actually earned gives the real
attainable maximum.
"""

from __future__ import annotations

import dataclasses
import json
import math
from pathlib import Path
from typing import Iterable, Sequence

from bench.verdict import Check, Verdict

SCORER_VERSION = "task-score-v4-mandatory-gated"


class ScoringError(Exception):
    """A task whose scoring is not well-formed. Never silently degrade."""


_LEGACY_DEFAULTS = {"l1": (1, 0), "l3": (1, 0), "hard": (0, -1), "soft": (0, 0)}


def gold_from_sidecar(task_dir: Path) -> Verdict:
    """Load the durable authored gold manifest for ``task_dir``.

    Evaluation must not depend on ephemeral ``/tmp`` gold verdicts.  The
    committed schema-2 ``empty-baseline.json`` contains gold outcome and
    scoring metadata for every authored check, which is sufficient to rescore
    a captured candidate verdict deterministically.
    """
    path = Path(task_dir) / "verifier" / "empty-baseline.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScoringError(f"cannot load durable gold manifest {path}: {exc}") from exc
    rows = data.get("checks_v2")
    if not isinstance(rows, list) or not rows:
        raise ScoringError(f"{path} has no non-empty checks_v2 gold manifest")
    completion = data.get("completion")
    if not isinstance(completion, dict):
        raise ScoringError(f"{path} has no explicit completion contract")
    required_rows = completion.get("required_checks")
    mode = completion.get("mode")
    if mode not in {"authored_mandatory_checks", "all_authored_gold_checks"} or not isinstance(
        required_rows, list
    ):
        raise ScoringError(f"{path} has an invalid completion contract")
    required = {str(name) for name in required_rows}
    authored = {str(row.get("name")) for row in rows if row.get("gold") is True}
    if mode == "authored_mandatory_checks":
        authored = {
            str(row.get("name"))
            for row in rows
            if row.get("gold") is True and row.get("mandatory") is True
        }
    if not required or required != authored:
        raise ScoringError(
            f"{path}: completion required_checks must equal the authored mandatory "
            "checks or all authored gold checks, according to mode"
        )
    verdict = Verdict(task=Path(task_dir).name, run_id="durable-gold-manifest")
    for row in rows:
        bucket = str(row.get("bucket", "l1"))
        target = getattr(verdict, bucket, None)
        if target is None:
            raise ScoringError(f"{path}: unknown check bucket {bucket!r}")
        default_pass, default_fail = _LEGACY_DEFAULTS.get(bucket, (1, 0))
        target.append(
            Check(
                name=str(row["name"]),
                ok=bool(row.get("gold")),
                pass_value=int(row.get("pass_value", default_pass)),
                fail_value=int(row.get("fail_value", default_fail)),
                mandatory=str(row["name"]) in required,
            )
        )
    report = validate_scoring(Path(task_dir).name, verdict)
    if not report.ok:
        raise ScoringError(f"{path}: invalid gold manifest: {'; '.join(report.errors)}")
    return verdict


def coverage(verdict: Verdict, gold: Verdict) -> tuple[int, int, float, list[str]]:
    """Return emitted expected names, total expected, ratio, and missing names."""
    expected = {c.name for c in dedupe(all_checks(gold))}
    emitted = {c.name for c in dedupe(all_checks(verdict))}
    missing = sorted(expected - emitted)
    hit = len(expected) - len(missing)
    return hit, len(expected), (hit / len(expected) if expected else 0.0), missing


def all_checks(verdict: Verdict) -> list[Check]:
    """Every recorded check, in a stable order. Soft checks are included: under
    this model a cosmetic check simply carries value 0/0 and contributes
    nothing, rather than being filtered by bucket."""
    return [*verdict.l1, *verdict.hard, *verdict.soft, *verdict.l3]


def dedupe(checks: Sequence[Check]) -> list[Check]:
    """Collapse repeated check NAMES to one worst-case instance.

    A check name identifies a property. Recording it once per scenario or once
    per vendor recreate does not make the property more important, but an
    un-deduped sum says it does: measured across the 50-task tree, **369 of 1697
    check instances (21.7%) are name repeats**, concentrated in conduct checks
    (hard 49.8%, soft 44.5%) because ``builtin_l2`` runs per recreate. Summing
    them would apply an unauthored multiplier — and for preserve-style checks
    (0 on pass, -1 on fail) it multiplies the PENALTY, so one conduct regression
    could cost -3 on a task that recreates three times and -1 on a task that
    recreates once, for identical behaviour.

    Worst-case, not first or last: if the property failed at any point during
    the run, it did not hold. Order within a name is otherwise preserved.
    """
    worst: dict[str, Check] = {}
    order: list[str] = []
    for c in checks:
        prev = worst.get(c.name)
        if prev is None:
            worst[c.name] = c
            order.append(c.name)
        elif prev.ok and not c.ok:
            # A later failure supersedes an earlier pass; keep the failing
            # instance so its detail is the one reported.
            worst[c.name] = c
    return [worst[n] for n in order]


def max_score(gold: Verdict) -> int:
    """The attainable maximum, measured on gold.

    Only positive contributions count. A preserve-style check (0 on pass, -1 on
    fail) adds nothing to the ceiling — it can only take away — which is exactly
    the intent.
    """
    return sum(c.value for c in dedupe(all_checks(gold)) if c.value > 0)


def checks_against_gold(verdict: Verdict, gold: Verdict) -> list[Check]:
    """Candidate outcomes projected onto GOLD's fixed authored check set.

    Gold is both the denominator and the manifest.  A candidate that exits
    before a check is emitted has not earned a neutral absence: it has failed
    that authored check and receives its authored ``fail_value``.  Candidate
    verdict metadata is deliberately not trusted for scoring; the verifier's
    frozen gold record owns pass/fail values and mandatory status.

    Extra candidate-only names are not scored.  They can arise on a
    failure-only branch gold does not traverse and are still considered by
    :func:`solved` when marked mandatory.
    """
    actual = {c.name: c for c in dedupe(all_checks(verdict))}
    projected: list[Check] = []
    for expected in dedupe(all_checks(gold)):
        got = actual.get(expected.name)
        projected.append(
            dataclasses.replace(
                expected,
                ok=bool(got and got.ok),
                detail=(got.detail if got else "missing required authored check"),
            )
        )
    return projected


def raw_score(verdict: Verdict, gold: Verdict | None = None) -> int:
    """Unclamped score, optionally over GOLD's fixed check manifest.

    Passing ``gold`` is required for authoritative evaluation.  The one-arg
    form remains for inspecting historical verdicts that have no durable gold
    artifact, but it cannot charge omitted checks.
    """
    checks = checks_against_gold(verdict, gold) if gold is not None else dedupe(all_checks(verdict))
    return sum(c.value for c in checks)


def task_score(verdict: Verdict, gold: Verdict) -> float:
    """Task Score in [0, 100].

    Raises ScoringError when the task cannot be scored, rather than returning a
    number nobody can interpret. A `max_score <= 0` means gold earned no
    positive value anywhere: the task has nothing to measure and must fail
    validation, not quietly score 0 for every submission (that is how a broken
    task masquerades as a hard one).
    """
    ceiling = max_score(gold)
    if ceiling <= 0:
        raise ScoringError(
            "max_score <= 0: gold earns no positive value on this task, so "
            "there is nothing to score against. Fix the task's check values."
        )
    required = [c for c in dedupe(all_checks(gold)) if c.mandatory]
    if required and not solved(verdict, gold):
        return 0.0
    raw = raw_score(verdict, gold)
    return 100.0 * _clamp(raw, 0, ceiling) / ceiling


def solved(verdict: Verdict, gold: Verdict | None = None) -> bool:
    """Strict completion against GOLD's authored mandatory manifest.

    A verdict that errored is never solved — a crashed grade is not a solve, and
    conflating the two is how lane faults have previously scored as results.

    A verdict that recorded NO mandatory check is likewise never solved. This is
    not the vacuous-task case (a task declaring no mandatory check at all is
    flagged by `validate_scoring`); it is the far more common one where the
    scenario bailed out early. Scenarios return early when their output is
    unreadable:

        code, _out, err = ctx.app.run(["push"])
        result = read_result(ctx)
        ctx.check("push_exit_ok", code == 0 and result is not None, ...)
        if result is None:
            ctx.check("result_readable", False, ...)
            return          # <-- every mandatory check below is never recorded

    A do-nothing submission takes exactly that path, so `all(...)` over an empty
    mandatory set would make the EMPTY probe vacuously Solved while scoring 0 —
    measured on task-0006, where all three scenarios bail this way. Never
    observing the required behaviour is not evidence of it.
    """
    if verdict.error:
        return False
    recorded = dedupe(all_checks(verdict))
    if gold is None:
        # Compatibility-only path for historical callers.  Production scoring
        # must pass gold; without it omitted mandatory checks are unknowable.
        mandatory = [c for c in recorded if c.mandatory]
        return bool(mandatory) and all(c.ok for c in mandatory)

    required = [c for c in dedupe(all_checks(gold)) if c.mandatory]
    if not required:
        return False
    by_name = {c.name: c for c in recorded}
    if not all(c.name in by_name and by_name[c.name].ok for c in required):
        return False
    # Gold is the floor, not the ceiling: a mandatory failure-path assertion
    # that gold never traverses must still block a solve when it is emitted.
    return all(c.ok for c in recorded if c.mandatory)


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# --- authoring validation -----------------------------------------------------


@dataclasses.dataclass
class ScoringReport:
    task: str
    max_score: int
    n_checks: int
    n_mandatory: int
    n_legacy: int
    errors: list[str] = dataclasses.field(default_factory=list)
    warnings: list[str] = dataclasses.field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_scoring(task: str, gold: Verdict, legacy_calls: int = 0) -> ScoringReport:
    """Check a task's scoring is well-formed, judged on its gold verdict.

    This is the mechanical half of the audit bar. It cannot tell you whether
    important behaviour scores higher than incidental (bar #1) — that needs a
    reader — but it does catch every structural way the scheme can be misused.
    """
    recorded = all_checks(gold)
    checks = dedupe(recorded)
    report = ScoringReport(
        task=task,
        max_score=max_score(gold),
        n_checks=len(checks),
        n_mandatory=sum(1 for c in checks if c.mandatory),
        n_legacy=legacy_calls,
    )

    if report.max_score <= 0:
        report.errors.append(
            f"max_score={report.max_score} (must be > 0): gold earns no positive value"
        )
    if not checks:
        report.errors.append("no checks recorded on gold")
    if report.n_mandatory == 0:
        report.errors.append(
            "no mandatory checks: Solved would be vacuously true for every "
            "submission, including the empty starter"
        )

    failed_on_gold = [c.name for c in checks if not c.ok]
    if failed_on_gold:
        report.errors.append(
            f"gold fails {len(failed_on_gold)} check(s), so Task Score cannot "
            f"reach 100: {failed_on_gold[:5]}"
        )

    # Measured on the RAW list: `checks` is already deduped, so this is
    # reporting how much repetition the scorer had to absorb, not a failure.
    # 44 of 50 tasks repeat at least one name (mostly conduct checks, once per
    # recreate), which is legitimate — but a name repeated with *different*
    # scoring is an authoring mistake the author almost certainly did not mean.
    dupes = _duplicates(c.name for c in recorded)
    if dupes:
        report.warnings.append(
            f"{len(dupes)} check name(s) recorded more than once; scored "
            f"worst-case-once: {sorted(dupes)[:5]}"
        )
        by_name: dict[str, set[tuple[int, int, bool]]] = {}
        for c in recorded:
            by_name.setdefault(c.name, set()).add((c.pass_value, c.fail_value, c.mandatory))
        conflicting = sorted(n for n, s in by_name.items() if len(s) > 1)
        if conflicting:
            report.errors.append(
                "same check name recorded with DIFFERENT scoring, so which "
                f"instance wins is arbitrary: {conflicting[:5]}"
            )

    inert = [c.name for c in checks if c.pass_value == 0 and c.fail_value == 0]
    if inert and len(inert) == len(checks):
        report.errors.append("every check is inert (0/0): nothing can move the score")
    elif inert:
        report.warnings.append(
            f"{len(inert)} inert check(s) (0/0) — intended only for cosmetic/advisory"
        )

    if report.n_legacy:
        report.warnings.append(
            f"{report.n_legacy} check(s) still recorded via legacy check_<bucket> "
            "calls and are carrying bucket defaults, not authored values"
        )
    return report


def _duplicates(names: Iterable[str]) -> set[str]:
    seen: set[str] = set()
    dupes: set[str] = set()
    for n in names:
        if n in seen:
            dupes.add(n)
        seen.add(n)
    return dupes


# --- suite metrics ------------------------------------------------------------


def bench_score(task_scores: Sequence[float]) -> float:
    """Mean Task Score across tasks — the headline percentage."""
    if not task_scores:
        return 0.0
    return sum(task_scores) / len(task_scores)


def wilson_interval(k: int, n: int, z: float = 1.959963984540054) -> tuple[float, float]:
    """95% Wilson score interval for k successes in n trials.

    Wilson rather than normal-approximation because at n=50 with rates near 0.2
    the normal interval runs off the end of [0, 1].
    """
    if n <= 0:
        return (0.0, 0.0)
    p = k / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    lo = max(0.0, centre - half)
    hi = min(1.0, centre + half)
    # The endpoints are exact for Wilson, but floating point leaves ~7e-18 at
    # k=0. Pin them so a zero rate reports 0.0 rather than scientific notation
    # in a results table.
    if k == 0:
        lo = 0.0
    if k == n:
        hi = 1.0
    return (lo, hi)


@dataclasses.dataclass
class SolveRate:
    solved: int
    total: int
    rate: float
    ci_low: float
    ci_high: float

    @property
    def ci_width(self) -> float:
        return self.ci_high - self.ci_low

    def format(self) -> str:
        return (
            f"{self.solved}/{self.total} = {self.rate:.3f} [{self.ci_low:.3f}, {self.ci_high:.3f}]"
        )


def solve_rate(solved_flags: Sequence[bool]) -> SolveRate:
    """Solve Rate with its 95% CI.

    The CI is not decoration. At n=50 it is ~0.25 wide, which is why an ordering
    like 0.400 vs 0.375 is not a result — reporting the rate without the
    interval invites exactly that misreading.
    """
    n = len(solved_flags)
    k = sum(1 for s in solved_flags if s)
    lo, hi = wilson_interval(k, n)
    return SolveRate(solved=k, total=n, rate=(k / n if n else 0.0), ci_low=lo, ci_high=hi)


# --- the audit bar, as code ---------------------------------------------------


@dataclasses.dataclass
class ProbeBar:
    """Audit-bar #5/#6 evaluated over a task's probe verdicts.

    The bar is four assertions, and every one of them has been violated by a
    real task in this suite at some point:

        gold  -> 100, Solved
        empty ->   0, unsolved
        stub  ->   0, unsolved
        naive -> loses meaningful points, unsolved

    Checking it by hand per task does not scale to 50 and has already let
    regressions through (task-0031 handed the empty starter 18.2/100 for merely
    executing, because plumbing checks had been scored +1 instead of
    preserve-style 0/-1).
    """

    task: str
    gold_score: float | None = None
    empty_score: float | None = None
    stub_score: float | None = None
    naive_score: float | None = None
    gold_solved: bool = False
    empty_solved: bool = False
    stub_solved: bool = False
    naive_solved: bool = False
    #: A naive submission must lose at least this share of the attainable score.
    naive_max_share: float = 0.75
    failures: list[str] = dataclasses.field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


def check_probe_bar(
    task: str,
    gold: Verdict,
    empty: Verdict | None = None,
    stub: Verdict | None = None,
    naive: Verdict | None = None,
    naive_max_share: float = 0.75,
) -> ProbeBar:
    """Evaluate audit bar #5 and #6. Absent probes are skipped, not passed."""
    bar = ProbeBar(task=task, naive_max_share=naive_max_share)

    bar.gold_score = task_score(gold, gold)
    bar.gold_solved = solved(gold, gold)
    # NOTE: `gold_score == 100` is true BY CONSTRUCTION — max_score sums the
    # positive values gold actually earned, so a check gold fails is simply
    # absent from the ceiling. Comparing the score can therefore never detect a
    # broken gold; `validate_scoring` is the only thing that can, so the bar
    # delegates to it rather than re-deriving a test that cannot fire.
    report = validate_scoring(task, gold)
    bar.failures.extend(report.errors)
    if not bar.gold_solved:
        bar.failures.append("gold is not Solved")

    for label, probe in (("empty", empty), ("stub", stub)):
        if probe is None:
            continue
        score, is_solved = task_score(probe, gold), solved(probe, gold)
        setattr(bar, f"{label}_score", score)
        setattr(bar, f"{label}_solved", is_solved)
        if score != 0.0:
            bar.failures.append(
                f"{label} scores {score:.1f}, must be 0.0 — a probe that does "
                "nothing useful is being paid for plumbing it did not write"
            )
        if is_solved:
            bar.failures.append(f"{label} is Solved, which must be impossible")

    if naive is not None:
        bar.naive_score = task_score(naive, gold)
        bar.naive_solved = solved(naive, gold)
        limit = 100.0 * naive_max_share
        if bar.naive_score > limit:
            bar.failures.append(
                f"naive scores {bar.naive_score:.1f} (> {limit:.1f}): a "
                "docs-faithful implementation is not losing meaningful points, "
                "so the task does not discriminate being fooled"
            )
        if bar.naive_solved:
            bar.failures.append("naive is Solved — the trap does not gate")
    return bar

"""`bench validate-suite` — the suite-level uniqueness lint.

Permanent gate against the "measured clone problem" (see WORKLOG): tasks that
are near-duplicates of each other in scenario shape, fault wiring, or check
structure erode the suite's coverage claims even though each one individually
passes `bench validate`. This command is pure static analysis over task
contracts, verifier scenarios/fixtures, participant materials, and authoring
metadata. It never touches Docker and never grades anything. Legacy Compose
inputs remain a read-only compatibility source for older suites, not the
canonical public-50 contract.

Six per-task signals feed the lint, each documented on its own computation
function below:

  a. primary_mechanic   — declared vocabulary slug, one task per mechanic.
  b. scenario_shape      — canonicalized, ordered scenario-name tuple.
  c. fault_profile       — FAULT_* env vars wired into the task.
  d. check_name_set       — verifier check names, entity-stemmed, compared
                            pairwise by Jaccard similarity.
  e. artifact_hashes      — comment-stripped, whitespace-normalized content
                            hashes of vendor.yaml / mutations.yaml / docs/ /
                            fixtures, for byte-clone detection.
  f. gate_topology        — best-effort tripwire for a scenario module that
                            both dominates a task's check count AND gates
                            almost all of it behind one early conditional
                            return (WARN only — see `_module_gate_flag` for
                            the heuristic's documented limits).
  g. check_evidence       — what each recorded check actually looked at,
                            traced back through the scenario AST. Catches a
                            task whose whole grading surface is
                            `output_file == golden_fixture` (FAIL
                            "vacuous_fixture_only_grading").

Optionally, with `--compare-tasks-dir`, one further signal is computed ACROSS
two suites: same vendor + same primary-mechanic family in both the public and
the held-out tree (WARN "cross_suite_mechanic_reuse"). Every other rule here
compares tasks within one tree, which is exactly why two independent audit
sweeps missed this class of duplicate.

Findings are leveled FAIL / WARN / INFO. `bench validate-suite` (default)
reports everything and exits 0. `bench validate-suite --enforce` exits 1 if
any FAIL finding exists — that's the CI gate.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
import json
import re
import shlex
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from bench.config import ConfigError, TaskConfig

CHECK_METHODS = ("check_l1", "check_hard", "check_soft", "check_l3")

# Names recorded by bench.verifier.builtin_l2 (shared, generic L2 gates) —
# excluded from check_name_set because every task that exercises the builtin
# gates would otherwise "share" these names with every other such task,
# which is a fixture of the harness, not evidence of task cloning.
_BUILTIN_L2_NAMES = frozenset(
    {
        "no_credentials_in_query_string",
        "no_secrets_echoed_to_vendor",
        "webhook_bad_signature_rejected",
        "webhook_stale_timestamp_rejected",
        "retry_after_honored",
        "no_hot_loop_on_error",
        "idempotent_write_retries",
    }
)
_BUILTIN_L2_PREFIXES = (
    "reauth_per_request",
    "no_unnecessary_full_resync",
    "resume_not_restart_on_retry",
)

# --- b. scenario_shape canonicalization -------------------------------------

_BACKFILL_EXACT = {
    "initial_sync",
    "initial_backfill",
    "full_backfill",
    "v1_backfill_baseline",
}
_INCREMENTAL_EXACT = {
    "incremental",
    "poll_incremental",
    "v2_incremental_watermark",
}
_WEBHOOK_FRESHNESS_RE = re.compile(r"webhook.*freshness")


def canonicalize_scenario_name(raw: str) -> str:
    """Map a task.yaml scenario entry (module filename or bare name) to a
    canonical mechanic-shape token, per the synonym table:

        initial_sync / initial_backfill / full_backfill /
          v1_backfill_baseline / legacy_baseline_*        -> backfill
        incremental / incremental_catchup* / poll_incremental /
          v2_incremental_watermark                          -> incremental
        *tamper*                                            -> tamper
        *webhook*freshness*                                 -> webhook_freshness
        *reconcile* / *reconverge*                          -> reconcile
        *writeback* / *write*                               -> writeback

    First match wins, checked in the order above. Anything that matches none
    of these is kept as-is (deliberately: most scenario names in this suite
    ARE unique to one task family, and canonicalizing too aggressively would
    manufacture false shape collisions instead of catching real ones).
    """
    name = raw.strip()
    if name.endswith(".py"):
        name = name[: -len(".py")]
    name = name.lower()

    if name in _BACKFILL_EXACT or name.startswith("legacy_baseline_"):
        return "backfill"
    if name in _INCREMENTAL_EXACT or name.startswith("incremental_catchup"):
        return "incremental"
    if "tamper" in name:
        return "tamper"
    if _WEBHOOK_FRESHNESS_RE.search(name):
        return "webhook_freshness"
    if "reconcil" in name or "reconverge" in name:
        return "reconcile"
    if "writeback" in name or "write" in name:
        return "writeback"
    return name


def scenario_shape(scenarios: list[str]) -> tuple[str, ...]:
    return tuple(canonicalize_scenario_name(s) for s in scenarios)


# --- c. fault_profile ---------------------------------------------------


def _task_fault_vars(task_dir: Path) -> set[str]:
    task_path = task_dir / "task.yaml"
    if task_path.is_file():
        data = yaml.safe_load(task_path.read_text(encoding="utf-8")) or {}
        roles = ((data.get("contract") or {}).get("runtime") or {}).get("vendor_roles") or {}
        found = {
            str(key)
            for role in roles.values()
            for key in ((role or {}).get("environment") or {})
            if str(key).startswith("FAULT_")
        }
        return found
    return set()


def _scenario_fault_literals(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value.startswith("FAULT_")
        ):
            found.add(node.value)
    return found


def compute_fault_profile(task_dir: Path, scenario_trees: dict[str, ast.AST]) -> frozenset[str]:
    found = _task_fault_vars(task_dir)
    for tree in scenario_trees.values():
        found |= _scenario_fault_literals(tree)
    return frozenset(found)


# --- d. check_name_set ---------------------------------------------------


def _is_excluded_builtin(name: str) -> bool:
    if name in _BUILTIN_L2_NAMES:
        return True
    return any(name.startswith(p) for p in _BUILTIN_L2_PREFIXES)


def _joined_str_template(node: ast.JoinedStr) -> str:
    """Render an f-string check name as a stable STRUCTURAL TEMPLATE.

    A per-row check name like::

        ctx.check_l1(f"outcome_{row['ref']}_is_{row['outcome']}", ...)

    is one static call site that records N runtime checks. The lint has to
    represent it by a single set element, so that element must at least carry
    the call site's literal structure. The previous implementation dropped
    every interpolation and joined the surviving constants with ``*``, which
    rendered the example above as ``outcome_*_is_`` — a token that (a) carries
    no information about what is being checked and (b) unifies with any other
    task whose f-string happens to have the same constant fragments, which
    both hides real differences and manufactures false overlap (measured on
    task-0053 vs task-0200, 2026-08-06 audit).

    Each interpolated slot now becomes a literal ``{}`` placeholder in place,
    preserving both the surrounding literals AND their arity/order:
    ``outcome_{}_is_{}``. Two call sites unify only if their literal skeleton
    and their number of interpolations both match.
    """
    parts: list[str] = []
    for value in node.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            parts.append(value.value)
        else:
            parts.append("{}")
    return "".join(parts)


def _raw_check_name(arg: ast.expr) -> str | None:
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, ast.JoinedStr):
        return _joined_str_template(arg)
    return None


_TRAILING_ID_RE = re.compile(r"::[^:]*$")

# A leading per-task id on a check name ("task-0058_app_exit_zero"). Generated
# scaffolds prefix EVERY check name with their own task id, which makes each
# task's check_name_set disjoint from every other task's by construction and
# drives check-name Jaccard to exactly 0.000 for pairs that are otherwise
# byte-identical in structure. Measured 2026-08-06: 184/200 held-out tasks did
# this; task-0058 vs task-0064 scored 0.000 raw and 0.750 (a FAIL) once the
# prefix was removed. The prefix is task identity, not task content, so it is
# stripped before similarity is computed.
_LEADING_TASK_ID_RE = re.compile(r"^task[-_]?0*\d+[-_]+")


def _strip_trailing_id_suffix(name: str) -> str:
    return _TRAILING_ID_RE.sub("", name)


def _strip_leading_task_id(name: str) -> str:
    return _LEADING_TASK_ID_RE.sub("", name)


def _entity_tokens(task_config: TaskConfig) -> set[str]:
    tokens: set[str] = set()
    for vendor_meta in task_config.vendors.values():
        for entity_name, edef in (vendor_meta.entities or {}).items():
            tokens.add(str(entity_name))
            plural = (edef or {}).get("plural")
            if plural:
                tokens.add(str(plural))
    return tokens


def _stem_entity_tokens(name: str, tokens: set[str]) -> str:
    stemmed = name
    for token in tokens:
        if not token:
            continue
        pattern = re.compile(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])")
        stemmed = pattern.sub("ENT", stemmed)
    return stemmed


def _extract_check_calls(tree: ast.AST) -> list[tuple[str, int]]:
    """(raw_name, lineno) for every check_l1/hard/soft/l3 call in `tree` whose
    first positional arg is a string constant or f-string; other call shapes
    (a name/variable holding the check name) are skipped — this signature is
    a static-analysis approximation, not an execution trace."""
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in CHECK_METHODS):
            continue
        if not node.args:
            continue
        raw = _raw_check_name(node.args[0])
        if raw is not None:
            out.append((raw, node.lineno))
    return out


def compute_check_name_set(
    scenario_trees: dict[str, ast.AST], entity_tokens: set[str]
) -> frozenset[str]:
    names: set[str] = set()
    for tree in scenario_trees.values():
        for raw, _lineno in _extract_check_calls(tree):
            if _is_excluded_builtin(raw):
                continue
            stemmed = _strip_trailing_id_suffix(raw)
            stemmed = _strip_leading_task_id(stemmed)
            stemmed = _stem_entity_tokens(stemmed, entity_tokens)
            names.add(stemmed)
    return frozenset(names)


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


# --- e. artifact_hashes ---------------------------------------------------


def _normalized_bytes(text: str) -> bytes:
    """Comment-stripped ('#' line comments), whitespace-normalized bytes used
    as the input to every artifact hash below. Collapses each line's internal
    whitespace, drops blank/comment-only lines, and joins what remains with a
    single space — a fingerprint that is stable across re-indentation and
    comment edits but changes on any substantive content change."""
    pieces = []
    for line in text.splitlines():
        content = line.split("#", 1)[0]
        collapsed = " ".join(content.split())
        if collapsed:
            pieces.append(collapsed)
    return " ".join(pieces).encode("utf-8")


def _sha256_normalized(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    return hashlib.sha256(_normalized_bytes(text)).hexdigest()


@dataclasses.dataclass
class ArtifactHashes:
    vendor_yaml: str | None
    mutations_yaml: str | None
    docs: dict[str, str]  # relpath (posix) -> hash
    fixtures: dict[str, str]  # filename -> hash


def compute_artifact_hashes(task_dir: Path) -> ArtifactHashes:
    vendor_yaml_path = task_dir / "vendor.yaml"
    mutations_yaml_path = task_dir / "mutations.yaml"

    docs: dict[str, str] = {}
    docs_dir = task_dir / "docs"
    if docs_dir.is_dir():
        for p in sorted(docs_dir.rglob("*")):
            if p.is_file():
                docs[p.relative_to(docs_dir).as_posix()] = _sha256_normalized(p)

    fixtures: dict[str, str] = {}
    fixtures_dir = task_dir / "verifier" / "fixtures"
    if fixtures_dir.is_dir():
        for p in sorted(fixtures_dir.glob("*.json")):
            fixtures[p.name] = _sha256_normalized(p)

    return ArtifactHashes(
        vendor_yaml=_sha256_normalized(vendor_yaml_path) if vendor_yaml_path.is_file() else None,
        mutations_yaml=_sha256_normalized(mutations_yaml_path)
        if mutations_yaml_path.is_file()
        else None,
        docs=docs,
        fixtures=fixtures,
    )


# --- f. gate_topology (approximate tripwire) --------------------------------

_FLAG_TOKENS = {"ok", "rc", "code", "success", "passed", "result"}


def _is_flag_name(name: str) -> bool:
    parts = name.lower().split("_")
    return any(p in _FLAG_TOKENS for p in parts)


def _contains_return(node: ast.AST) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Return):
            return True
    return False


def _names_in(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


@dataclasses.dataclass
class ModuleGateInfo:
    total_checks: int
    checks_before_gate: int | None  # None if no qualifying conditional-return found


def _module_gate_flag(tree: ast.AST) -> ModuleGateInfo:
    """Best-effort AST heuristic (documented, not exact):

    Finds the EARLIEST `if` statement (by source line) whose test references
    a variable that either (a) was assigned directly from a check_l1/hard/
    soft/l3 call, or (b) has an "ok/rc-style" name (a `_`-delimited token
    equal to ok/rc/code/success/passed/result) — provided that variable was
    assigned at an earlier line than the `if`, and the `if`'s body contains a
    `return` anywhere in it.

    Known limits (by design — this is a tripwire, not a verifier):
      - only catches variable-gated returns, not e.g. `if not ctx.ok(): return`
        or gating via exceptions/early raises;
      - does not follow control flow across function boundaries or through
        helper modules (_scenario_util.py etc);
      - "assigned at an earlier line" is a textual heuristic, not a real
        reaching-definitions analysis (a variable reassigned later in a loop
        body, for instance, is not modeled);
      - counts check-calls by raw AST position, including ones this module's
        check_name_set computation excludes as builtin-L2 names.
    """
    check_lines = sorted(lineno for _raw, lineno in _extract_check_calls(tree))
    total = len(check_lines)

    flagged_by_line: dict[str, int] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if not isinstance(target, ast.Name):
                    continue
                qualifies = _is_flag_name(target.id)
                if not qualifies and isinstance(node.value, ast.Call):
                    func = node.value.func
                    if isinstance(func, ast.Attribute) and func.attr in CHECK_METHODS:
                        qualifies = True
                if qualifies:
                    prev = flagged_by_line.get(target.id)
                    if prev is None or node.lineno < prev:
                        flagged_by_line[target.id] = node.lineno

    earliest_if_line: int | None = None
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        if earliest_if_line is not None and node.lineno >= earliest_if_line:
            continue
        test_names = _names_in(node.test)
        gating_names = test_names & flagged_by_line.keys()
        if not gating_names:
            continue
        if any(flagged_by_line[n] >= node.lineno for n in gating_names):
            continue
        body_has_return = any(_contains_return(stmt) for stmt in node.body)
        if not body_has_return:
            continue
        earliest_if_line = node.lineno

    if earliest_if_line is None:
        return ModuleGateInfo(total_checks=total, checks_before_gate=None)

    checks_before = sum(1 for ln in check_lines if ln < earliest_if_line)
    return ModuleGateInfo(total_checks=total, checks_before_gate=checks_before)


# --- g. check_evidence (vacuity tripwire) ----------------------------------
#
# What a check is ALLOWED to look at, and what it actually looked at.
#
# The 200-task expansion was generated from a scaffold whose entire grading
# surface is "run the app, then compare each output file to a golden fixture
# byte-for-byte". That grades the deliverable's bytes and nothing else: it
# cannot see whether the connector paginated correctly, honored Retry-After,
# re-sent rows that were already current, or resumed instead of restarting —
# i.e. it cannot see any of the conduct the suite exists to measure. Every
# other rule in this module is a CLONE detector; a fixture-only task is not
# necessarily a clone of anything, so nothing here saw it. This is that gate.
#
# The analysis is a small, bounded evidence tracer over the scenario AST: for
# each recorded check, resolve the boolean it was given back to the sources it
# was computed from, and label those sources. It is deliberately biased toward
# FALSE NEGATIVES -- any source it cannot resolve is treated as real evidence,
# so a task is only reported when the tracer can positively account for every
# one of its checks.

# `handle.request_log()` / `.token_log()` / `.webhook_deliveries()` /
# `.state(...)` -- the vendor-side observation surface (bench.verifier.context
# VendorHandle, bench.verifier.control_client). Evidence about what the
# connector DID, as opposed to what it emitted.
_VENDOR_EVIDENCE_CALLS = frozenset({"request_log", "token_log", "webhook_deliveries", "state"})
_FILE_READ_CALLS = frozenset({"read_text", "read_bytes", "load", "loads", "reader", "DictReader"})

# Names that carry no evidence of their own and must not be treated as
# "unresolved" (which would conservatively mark a check as substantive).
_INERT_NAMES = frozenset(
    {
        "ctx",
        "json",
        "csv",
        "io",
        "os",
        "re",
        "sys",
        "math",
        "Path",
        "datetime",
        "builtin_l2",
        "True",
        "False",
        "None",
        "len",
        "set",
        "sorted",
        "any",
        "all",
        "str",
        "int",
        "float",
        "bool",
        "list",
        "dict",
        "tuple",
        "abs",
        "min",
        "max",
        "sum",
        "zip",
        "enumerate",
        "range",
        "isinstance",
        "repr",
        "type",
    }
)

_MAX_EVIDENCE_DEPTH = 8

# Evidence labels produced by _EvidenceTracer.kinds():
#   "vendor"   -- vendor request log / token log / webhook deliveries / state
#   "fixture"  -- a path under ctx.fixtures (a golden answer file)
#   "output"   -- a path under ctx.output_dir (the deliverable)
#   "app"      -- ctx.app (exit code, stdout, stderr)
#   "fileread" -- whole-file slurp (read_text/json.load/...)
#   "unknown"  -- an unresolvable name; forces the check to count as real


class _EvidenceTracer:
    """Resolves the `ok` expression of a check call back to its evidence kinds.

    Handles the three shapes the suite actually uses: an inline expression, a
    local variable assigned earlier in `run()`, and a module-level comparison
    helper called with `ctx.output_dir` / `ctx.fixtures` paths (the scaffold's
    `_compare_json(a, b)` shape -- parameters are bound to the caller's
    argument kinds so the fixture reference at the CALL SITE is what labels
    the helper's return value).

    Tuple positions are tracked, because the dominant shape in this suite is
    `ok, detail = _compare_json(out, golden)` -- without positional tracking
    the free-text `detail` half of the return tuple contributes its own names
    to the verdict and every check comes back "unknown".

    Limits, by design: assignments are unioned across the whole module rather
    than tracked per control-flow path (so a name reused for two different
    things gets both labels), locals of different functions share one name
    space, recursion is depth-capped, and nothing is followed across module
    boundaries. Every one of those limits pushes toward "unknown", which
    SUPPRESSES the vacuity finding rather than raising it.
    """

    def __init__(self, tree: ast.AST) -> None:
        self.funcs: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        # name -> [(source expression, tuple index or None)]
        self.assigns: dict[str, list[tuple[ast.expr, int | None]]] = defaultdict(list)
        # `except X as exc:` names bind exception objects, not evidence.
        self.inert_locals: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.funcs[node.name] = node
            elif isinstance(node, ast.ExceptHandler) and node.name:
                self.inert_locals.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    self._bind(target, node.value)
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)) and node.value is not None:
                self._bind(node.target, node.value)
            elif isinstance(node, (ast.For, ast.AsyncFor)):
                for name in self._target_names(node.target):
                    self.assigns[name].append((node.iter, None))
            elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                for name in self._target_names(node.optional_vars):
                    self.assigns[name].append((node.context_expr, None))

    def _bind(self, target: ast.expr, value: ast.expr) -> None:
        if isinstance(target, (ast.Tuple, ast.List)):
            elts = list(target.elts)
            if isinstance(value, (ast.Tuple, ast.List)) and len(value.elts) == len(elts):
                for sub_target, sub_value in zip(elts, value.elts):
                    self._bind(sub_target, sub_value)
                return
            for index, sub_target in enumerate(elts):
                if isinstance(sub_target, ast.Name):
                    self.assigns[sub_target.id].append((value, index))
                else:
                    for name in self._target_names(sub_target):
                        self.assigns[name].append((value, None))
            return
        if isinstance(target, ast.Name):
            self.assigns[target.id].append((value, None))
            return
        for name in self._target_names(target):
            self.assigns[name].append((value, None))

    @staticmethod
    def _target_names(target: ast.expr) -> list[str]:
        return [n.id for n in ast.walk(target) if isinstance(n, ast.Name)]

    def kinds(
        self,
        expr: ast.expr,
        env: dict[str, frozenset[str]] | None = None,
        depth: int = 0,
        seen: frozenset[str] = frozenset(),
        index: int | None = None,
    ) -> set[str]:
        if depth > _MAX_EVIDENCE_DEPTH:
            return {"unknown"}
        env = env or {}
        out: set[str] = set()

        # Projecting one element out of a literal tuple return. Structural
        # descent, so `depth` is NOT incremented -- the cap bounds resolution
        # steps (name lookups and helper calls), not AST nesting. Counting AST
        # nesting exhausted the budget inside a two-line comparison helper and
        # silently returned "unknown" for every check.
        if index is not None and isinstance(expr, (ast.Tuple, ast.List)):
            if index < len(expr.elts):
                return self.kinds(expr.elts[index], env, depth, seen)
            return {"unknown"}

        # A call into a module-level helper: bind params to the caller's
        # argument kinds and evaluate the helper's returns in that scope.
        if (
            isinstance(expr, ast.Call)
            and isinstance(expr.func, ast.Name)
            and expr.func.id in self.funcs
        ):
            arg_kinds = [frozenset(self.kinds(a, env, depth, seen)) for a in expr.args]
            for a in expr.args:
                out |= self.kinds(a, env, depth, seen)
            out |= self._call(self.funcs[expr.func.id], arg_kinds, depth + 1, seen, index)
            return out

        if isinstance(expr, ast.Attribute):
            if expr.attr in _VENDOR_EVIDENCE_CALLS:
                out.add("vendor")
            elif expr.attr == "fixtures":
                out.add("fixture")
            elif expr.attr == "output_dir":
                out.add("output")
            elif expr.attr == "app":
                out.add("app")
            elif (
                expr.attr == "vendor"
                and isinstance(expr.value, ast.Name)
                and expr.value.id == "ctx"
            ):
                out.add("vendor")
            elif expr.attr in _FILE_READ_CALLS:
                out.add("fileread")

        if isinstance(expr, ast.Name):
            name = expr.id
            if name in env:
                out |= set(env[name])
            elif name in seen or name in _INERT_NAMES or name in self.funcs:
                pass
            elif name in self.inert_locals:
                pass
            elif name in self.assigns:
                for sub, sub_index in self.assigns[name]:
                    out |= self.kinds(sub, env, depth + 1, seen | {name}, sub_index)
            elif name.startswith("_") or name.isupper():
                pass
            else:
                out.add("unknown")
            return out

        for child in ast.iter_child_nodes(expr):
            if isinstance(child, ast.expr):
                out |= self.kinds(child, env, depth, seen)
        return out

    def _call(
        self,
        fn: ast.FunctionDef | ast.AsyncFunctionDef,
        arg_kinds: list[frozenset[str]],
        depth: int,
        seen: frozenset[str],
        index: int | None = None,
    ) -> set[str]:
        if depth > _MAX_EVIDENCE_DEPTH or fn.name in seen:
            return {"unknown"}
        params = [a.arg for a in fn.args.args]
        env = {p: arg_kinds[i] for i, p in enumerate(params) if i < len(arg_kinds)}
        out: set[str] = set()
        inner_seen = seen | {fn.name}
        returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return) and n.value is not None]
        if not returns:
            return {"unknown"}
        for ret in returns:
            out |= self.kinds(ret.value, env, depth + 1, inner_seen, index)
        return out


# Per-check evidence verdicts.
EVIDENCE_TRIVIAL = "trivial"  # exit code / file existence only
EVIDENCE_FIXTURE_BLOB = "fixture_blob"  # whole-file compare against a golden
EVIDENCE_SUBSTANTIVE = "substantive"  # vendor evidence, or anything unresolved


def classify_check_evidence(kinds: set[str]) -> str:
    if "vendor" in kinds or "unknown" in kinds:
        return EVIDENCE_SUBSTANTIVE
    if "fixture" in kinds:
        return EVIDENCE_FIXTURE_BLOB
    if kinds <= {"app", "output"}:
        return EVIDENCE_TRIVIAL
    return EVIDENCE_SUBSTANTIVE


@dataclasses.dataclass
class EvidenceProfile:
    trivial: int = 0
    fixture_blob: int = 0
    substantive: int = 0

    @property
    def total(self) -> int:
        return self.trivial + self.fixture_blob + self.substantive

    @property
    def is_vacuous(self) -> bool:
        """Every recorded check is either an exit-code/existence check or a
        whole-file fixture compare, and at least one IS a fixture compare."""
        return self.substantive == 0 and self.fixture_blob >= 1


def compute_evidence_profile(scenario_trees: dict[str, ast.AST]) -> EvidenceProfile:
    profile = EvidenceProfile()
    for tree in scenario_trees.values():
        tracer = _EvidenceTracer(tree)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr in CHECK_METHODS):
                continue
            if len(node.args) < 2:
                continue
            raw = _raw_check_name(node.args[0])
            if raw is not None and _is_excluded_builtin(raw):
                continue
            verdict = classify_check_evidence(tracer.kinds(node.args[1]))
            if verdict == EVIDENCE_TRIVIAL:
                profile.trivial += 1
            elif verdict == EVIDENCE_FIXTURE_BLOB:
                profile.fixture_blob += 1
            else:
                profile.substantive += 1
    return profile


# --- Task signature -----------------------------------------------------


@dataclasses.dataclass
class TaskSignature:
    task_id: str
    task_dir: Path
    vendor: str
    primary_mechanic: str | None
    scenario_shape: tuple[str, ...]
    fault_profile: frozenset[str]
    check_name_set: frozenset[str]
    artifact_hashes: ArtifactHashes
    module_gates: dict[str, ModuleGateInfo]  # scenario filename -> gate info
    evidence: EvidenceProfile = dataclasses.field(default_factory=EvidenceProfile)
    errors: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class Finding:
    level: str  # "FAIL" | "WARN" | "INFO"
    rule: str
    tasks: list[str]
    message: str

    def render(self) -> str:
        return f"[{self.level}] {self.rule}: {', '.join(self.tasks)} -- {self.message}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "rule": self.rule,
            "tasks": list(self.tasks),
            "message": self.message,
        }


def discover_tasks(tasks_dir: Path) -> list[Path]:
    tasks_dir = Path(tasks_dir)
    if not tasks_dir.is_dir():
        return []
    return sorted(p for p in tasks_dir.iterdir() if p.is_dir() and (p / "task.yaml").is_file())


def _load_scenario_trees(task_dir: Path) -> tuple[dict[str, ast.AST], list[str]]:
    trees: dict[str, ast.AST] = {}
    errors: list[str] = []
    scenarios_dir = task_dir / "verifier" / "scenarios"
    if not scenarios_dir.is_dir():
        return trees, errors
    for p in sorted(scenarios_dir.glob("*.py")):
        try:
            source = p.read_text(encoding="utf-8")
            trees[p.name] = ast.parse(source, filename=str(p))
        except (SyntaxError, UnicodeDecodeError, OSError) as exc:
            errors.append(f"{p.name}: failed to parse ({exc})")
    return trees, errors


def load_mechanics(path: Path | None = None) -> dict[str, str]:
    path = path or (Path(__file__).resolve().parents[1] / "mechanics.yaml")
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items()}


def build_task_signature(task_dir: Path) -> TaskSignature:
    task_dir = Path(task_dir)
    errors: list[str] = []
    try:
        config = TaskConfig.load(task_dir)
    except ConfigError as exc:
        errors.append(f"task.yaml: {exc}")
        return TaskSignature(
            task_id=task_dir.name,
            task_dir=task_dir,
            vendor="",
            primary_mechanic=None,
            scenario_shape=(),
            fault_profile=frozenset(),
            check_name_set=frozenset(),
            artifact_hashes=ArtifactHashes(None, None, {}, {}),
            module_gates={},
            errors=errors,
        )

    scenario_trees, parse_errors = _load_scenario_trees(task_dir)
    errors.extend(parse_errors)

    entity_tokens = _entity_tokens(config)
    shape = scenario_shape(config.scenarios or [])
    fault_profile = compute_fault_profile(task_dir, scenario_trees)
    check_names = compute_check_name_set(scenario_trees, entity_tokens)
    artifact_hashes = compute_artifact_hashes(task_dir)
    module_gates = {name: _module_gate_flag(tree) for name, tree in scenario_trees.items()}
    evidence = compute_evidence_profile(scenario_trees)

    primary_mechanic = config.raw.get("primary_mechanic")
    if primary_mechanic is not None:
        primary_mechanic = str(primary_mechanic)

    return TaskSignature(
        task_id=config.id,
        task_dir=task_dir,
        vendor=config.vendor,
        primary_mechanic=primary_mechanic,
        scenario_shape=shape,
        fault_profile=fault_profile,
        check_name_set=check_names,
        artifact_hashes=artifact_hashes,
        module_gates=module_gates,
        evidence=evidence,
        errors=errors,
    )


# --- Cross-task findings --------------------------------------------------


def _finding_mechanic(signatures: list[TaskSignature], mechanics: dict[str, str]) -> list[Finding]:
    findings: list[Finding] = []
    declared: dict[str, list[str]] = defaultdict(list)
    for sig in signatures:
        if sig.primary_mechanic is None:
            findings.append(
                Finding(
                    level="WARN",
                    rule="undeclared_mechanic",
                    tasks=[sig.task_id],
                    message="no primary_mechanic: declared in task.yaml",
                )
            )
            continue
        if sig.primary_mechanic not in mechanics:
            findings.append(
                Finding(
                    level="FAIL",
                    rule="unknown_mechanic",
                    tasks=[sig.task_id],
                    message=f"primary_mechanic {sig.primary_mechanic!r} is not in mechanics.yaml",
                )
            )
            continue
        declared[sig.primary_mechanic].append(sig.task_id)

    for mechanic, task_ids in sorted(declared.items()):
        if len(task_ids) >= 2:
            findings.append(
                Finding(
                    level="FAIL",
                    rule="duplicate_mechanic",
                    tasks=sorted(task_ids),
                    message=f"primary_mechanic {mechanic!r} declared by {len(task_ids)} tasks",
                )
            )
    return findings


# --- cross-suite mechanic reuse (public <-> held-out) -----------------------
#
# Generic slug tokens: they describe the SHAPE of almost any connector
# mechanic ("sync", "import", "handling", "on", "per") and so carry no
# signal about which mechanic it is. Dropping them keeps the family key
# anchored on the distinguishing nouns.
_MECHANIC_STOPWORDS = frozenset(
    {
        "and",
        "for",
        "from",
        "into",
        "not",
        "the",
        "via",
        "with",
        "when",
        "then",
        "per",
        "run",
        "mid",
        "only",
        "under",
        "over",
        "after",
        "before",
        "sync",
        "import",
        "handling",
        "handle",
        "based",
    }
)


def mechanic_family_tokens(slug: str) -> frozenset[str]:
    """Content tokens of a primary_mechanic slug, generic filler removed."""
    tokens = [t for t in re.split(r"[^a-z0-9]+", slug.lower()) if t]
    return frozenset(t for t in tokens if len(t) > 2 and t not in _MECHANIC_STOPWORDS)


def same_mechanic_family(slug_a: str, slug_b: str) -> bool:
    """True when two mechanic slugs name the same underlying exercise.

    Identical slugs always match. Otherwise the two token sets must share at
    least TWO content tokens and overlap at Jaccard >= 0.35 -- i.e. the two
    slugs are largely the same words about the same thing, not neighbors that
    happen to share one noun.

    Both numbers are calibrated against the measured public-vs-held-out
    corpus (434 same-vendor cross-suite slug pairs, 2026-08-06), which
    separates cleanly:

      - exactly ONE pair shares two content tokens -- public task-0001
        `hmac_clock_skew` vs held-out task-0191
        `container_clock_hmac_failure`, sharing {clock, hmac} at J=0.400.
        Same vendor, same HMAC-under-clock-skew exercise: a true positive.
      - every other pair shares exactly one token and sits at J<=0.25,
        including public task-0030 `conflict_refetch_retry` vs held-out
        task-0172 `conflict_body_reuse_efficiency` (shares only `conflict`).
        That pair WAS a duplicate and was deliberately redesigned in audit
        pass 3, so it should no longer fire -- and does not.

    The two-shared-token requirement is the discriminator; the 0.35 floor
    keeps a pair of long slugs from qualifying on two incidental tokens (two
    3-token slugs sharing two words score 0.50, a 3-and-4 token pair 0.40, a
    3-and-5 token pair 0.33 and is rejected).
    """
    if slug_a == slug_b:
        return True
    ta, tb = mechanic_family_tokens(slug_a), mechanic_family_tokens(slug_b)
    if not ta or not tb:
        return False
    shared = ta & tb
    if len(shared) < 2:
        return False
    return len(shared) / len(ta | tb) >= 0.35


def _finding_cross_suite_mechanic(
    signatures: list[TaskSignature],
    compare_signatures: list[TaskSignature],
    compare_label: str,
) -> list[Finding]:
    """Same vendor + same primary-mechanic family across two suites (WARN).

    Requested in STATUS.md item 10 after the third audit pass of the 200-task
    held-out catalog: pass 3 found held-out 0172 duplicating public 0030 and
    0236 duplicating public 0014, both same-vendor same-mechanic, and BOTH
    were missed by two independent agent sweeps that only ever compared tasks
    within one suite. A held-out task that re-runs a public task's mechanic on
    the same vendor is contaminated -- it measures how well a model memorized
    the public suite, not whether it generalizes.

    WARN rather than FAIL: a shared vendor is legitimate and near-slug matches
    need a human read, so this reports rather than blocks.
    """
    findings: list[Finding] = []
    by_vendor: dict[str, list[TaskSignature]] = defaultdict(list)
    for sig in compare_signatures:
        if sig.primary_mechanic:
            by_vendor[sig.vendor].append(sig)

    for sig in signatures:
        if not sig.primary_mechanic:
            continue
        for other in by_vendor.get(sig.vendor, []):
            if not same_mechanic_family(sig.primary_mechanic, other.primary_mechanic or ""):
                continue
            findings.append(
                Finding(
                    level="WARN",
                    rule="cross_suite_mechanic_reuse",
                    tasks=[sig.task_id, f"{compare_label}:{other.task_id}"],
                    message=(
                        f"vendor {sig.vendor!r} + mechanic family shared across suites: "
                        f"{sig.primary_mechanic!r} vs {compare_label} "
                        f"{other.primary_mechanic!r}"
                    ),
                )
            )
    return findings


def _finding_patch_applies(signatures: list[TaskSignature]) -> list[Finding]:
    """`solution.patch` must apply cleanly INSIDE the task's `repo/` directory.

    `bench.workspace.apply_patch` runs `git apply` with `cwd=repo_dir`, so diff
    headers must be repo-root-relative (`a/src/...`), NOT task-relative
    (`a/repo/src/...`). A task-relative patch fails to apply, gold gets an error
    verdict, and `validate` pins the denominator to gold's check count — so
    EVERY probe fraction comes out 0.0 with n=0 and the failure looks like a
    grading crash rather than a bad patch. Measured on task-0004, 2026-08-01;
    it cost a full serial gauntlet slot to diagnose. This lint is Docker-free
    and catches it in milliseconds.
    """
    findings: list[Finding] = []
    for sig in signatures:
        patch = sig.task_dir / "authoring" / "solution.patch"
        repo = sig.task_dir / "repo"
        if not patch.is_file() or not repo.is_dir():
            continue
        text = patch.read_text(encoding="utf-8", errors="replace")
        bad = [
            ln
            for ln in text.splitlines()
            if ln.startswith("diff --git a/repo/") or ln.startswith("--- a/repo/")
        ]
        if bad:
            findings.append(
                Finding(
                    level="FAIL",
                    rule="patch_paths_not_repo_relative",
                    tasks=[sig.task_id],
                    message=(
                        f"solution.patch has {len(bad)} task-relative diff header(s) "
                        f"(e.g. {bad[0][:60]!r}); apply_patch runs `git apply` inside "
                        "repo/, so headers must omit the repo/ prefix"
                    ),
                )
            )
    return findings


def _finding_docker_copy_sources(signatures: list[TaskSignature]) -> list[Finding]:
    """Every local Docker ``COPY`` source must survive a clean checkout.

    Docker accepts an empty directory from a developer's working tree, but Git
    does not preserve empty directories.  It can therefore build while
    authoring and fail in the pristine workspace used for grading.  Task 0049
    exposed this exact gap when its Dockerfile copied an empty, untracked
    ``tests/`` directory.

    This parser deliberately handles the shell-form COPY instructions used by
    the suite.  Remote and ``--from`` copies are outside the task build context.
    """
    findings: list[Finding] = []
    for sig in signatures:
        repo = sig.task_dir / "repo"
        dockerfile = repo / "Dockerfile"
        if not dockerfile.is_file():
            continue

        try:
            git_root_result = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
                check=False,
            )
            git_root = (
                Path(git_root_result.stdout.strip()).resolve()
                if git_root_result.returncode == 0
                else None
            )
        except OSError:
            git_root = None

        for lineno, raw in enumerate(dockerfile.read_text(encoding="utf-8").splitlines(), 1):
            stripped = raw.strip()
            if not stripped.upper().startswith("COPY "):
                continue
            try:
                words = shlex.split(stripped)
            except ValueError:
                continue
            if len(words) < 3 or any(word.startswith("--from=") for word in words[1:-1]):
                continue
            for source in (word for word in words[1:-1] if not word.startswith("--")):
                if source.startswith(("http://", "https://")) or any(c in source for c in "*?["):
                    continue
                source_path = (repo / source).resolve()
                has_file = source_path.is_file() or (
                    source_path.is_dir() and any(path.is_file() for path in source_path.rglob("*"))
                )
                tracked = True
                if has_file and git_root is not None:
                    try:
                        relative = source_path.relative_to(git_root)
                    except ValueError:
                        tracked = False
                    else:
                        tracked_result = subprocess.run(
                            [
                                "git",
                                "-C",
                                str(git_root),
                                "ls-files",
                                "--error-unmatch",
                                "--",
                                str(relative),
                            ],
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        tracked = tracked_result.returncode == 0 and bool(
                            tracked_result.stdout.strip()
                        )
                if has_file and tracked:
                    continue
                reason = "missing or empty" if not has_file else "not tracked by Git"
                findings.append(
                    Finding(
                        level="FAIL",
                        rule="docker_copy_source_not_pristine",
                        tasks=[sig.task_id],
                        message=(
                            f"repo/Dockerfile:{lineno} COPY source {source!r} is {reason}; "
                            "the candidate image must build from a clean tracked workspace"
                        ),
                    )
                )
    return findings


def _finding_missing_base_path(signatures: list[TaskSignature]) -> list[Finding]:
    """`vendors.<v>.vendor.base_path` must be declared EXPLICITLY (it may be
    "" for a vendor that serves at root). When it is absent, builtin_l2 builds
    every per-entity list path as "/<plural>" instead of
    "<base_path>/<plural>", so the per-entity soft checks watch paths the
    vendor never serves and silently never fire — a scoring hole that looks
    like nothing at all. Measured on task-0019 (rosterly /api, 4 dead checks
    per scenario) and found again on task-0008/task-0017 by audit,
    2026-08-01."""
    findings: list[Finding] = []
    for sig in signatures:
        try:
            config = TaskConfig.load(sig.task_dir)
        except ConfigError:
            continue
        for vendor_name, vendor_meta in (config.vendors or {}).items():
            raw = vendor_meta.raw or {}
            vendor_block = raw.get("vendor")
            if not isinstance(vendor_block, dict) or "base_path" not in vendor_block:
                findings.append(
                    Finding(
                        level="FAIL",
                        rule="missing_vendor_base_path",
                        tasks=[sig.task_id],
                        message=(
                            f"vendors.{vendor_name}.vendor.base_path is not declared; "
                            "per-entity conduct checks will watch the wrong paths and "
                            'never fire (declare "" explicitly for root-served vendors)'
                        ),
                    )
                )
    return findings


def _finding_vacuous_grading(signatures: list[TaskSignature]) -> list[Finding]:
    """A task whose ENTIRE grading surface is `output_file == golden_fixture`.

    Such a task measures whether the deliverable's bytes match, and nothing
    else. None of the conduct this suite exists to score -- pagination,
    Retry-After, resume-not-restart, no-redundant-writes, delivery behavior --
    is observable from the output bytes, so a fixture-only task awards full
    credit to a connector that got the right answer the wrong way, and awards
    zero to one that behaved correctly but formatted a field differently.

    FAILs when the evidence tracer can account for every recorded check and
    finds no substantive one: at least one whole-file fixture comparison, zero
    checks that consult the vendor request log / token log / webhook
    deliveries / vendor state, and zero checks the tracer could not resolve.
    Note that vendor evidence used for something OTHER than grading (e.g.
    reading `request_log()` only to build an exclusion list for `builtin_l2`)
    does not clear this bar -- the scaffold does exactly that, and it is still
    not grading on it.
    """
    findings: list[Finding] = []
    for sig in signatures:
        if not sig.evidence.is_vacuous:
            continue
        findings.append(
            Finding(
                level="FAIL",
                rule="vacuous_fixture_only_grading",
                tasks=[sig.task_id],
                message=(
                    f"all {sig.evidence.total} scenario check(s) grade on whole-file "
                    f"fixture comparison ({sig.evidence.fixture_blob} blob compare(s), "
                    f"{sig.evidence.trivial} exit/existence check(s)) with no "
                    "request-log, vendor-state or webhook-delivery evidence feeding "
                    "any check"
                ),
            )
        )
    return findings


def _finding_scenario_shape(signatures: list[TaskSignature]) -> list[Finding]:
    findings: list[Finding] = []
    by_shape: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for sig in signatures:
        if sig.scenario_shape:
            by_shape[sig.scenario_shape].append(sig.task_id)

    for shape, task_ids in sorted(by_shape.items()):
        if len(task_ids) > 3:
            findings.append(
                Finding(
                    level="FAIL",
                    rule="scenario_shape_overused",
                    tasks=sorted(task_ids),
                    message=f"canonical scenario_shape {shape!r} shared by {len(task_ids)} tasks (>3)",
                )
            )
    return findings


def _finding_fault_scenario_profile(signatures: list[TaskSignature]) -> list[Finding]:
    findings: list[Finding] = []
    by_triple: dict[tuple[str, frozenset[str], tuple[str, ...]], list[str]] = defaultdict(list)
    for sig in signatures:
        key = (sig.vendor, sig.fault_profile, sig.scenario_shape)
        by_triple[key].append(sig.task_id)

    for (vendor, faults, shape), task_ids in sorted(by_triple.items(), key=lambda kv: kv[0][0]):
        if len(task_ids) >= 2:
            findings.append(
                Finding(
                    level="FAIL",
                    rule="duplicate_fault_scenario_profile",
                    tasks=sorted(task_ids),
                    message=(
                        f"identical (vendor={vendor!r}, fault_profile={sorted(faults)!r}, "
                        f"scenario_shape={shape!r}) shared by {len(task_ids)} tasks"
                    ),
                )
            )
    return findings


def _all_pairs_jaccard(signatures: list[TaskSignature]) -> list[tuple[str, str, float]]:
    pairs: list[tuple[str, str, float]] = []
    for i in range(len(signatures)):
        for j in range(i + 1, len(signatures)):
            a, b = signatures[i], signatures[j]
            score = jaccard(a.check_name_set, b.check_name_set)
            pairs.append((a.task_id, b.task_id, score))
    pairs.sort(key=lambda t: t[2], reverse=True)
    return pairs


def _finding_check_name_jaccard(all_pairs: list[tuple[str, str, float]]) -> list[Finding]:
    findings: list[Finding] = []
    for task_a, task_b, score in all_pairs:
        if score > 0.55:
            findings.append(
                Finding(
                    level="FAIL",
                    rule="check_name_jaccard_high",
                    tasks=[task_a, task_b],
                    message=f"check-name Jaccard similarity {score:.3f} > 0.55",
                )
            )
        elif score > 0.40:
            findings.append(
                Finding(
                    level="WARN",
                    rule="check_name_jaccard_moderate",
                    tasks=[task_a, task_b],
                    message=f"check-name Jaccard similarity {score:.3f} > 0.40",
                )
            )
    return findings


def _clone_groups(items: dict[str, str]) -> dict[str, list[str]]:
    """items: task_id -> hash (entries with hash None are skipped). Returns
    hash -> sorted task_ids, filtered to groups of size >= 2."""
    by_hash: dict[str, list[str]] = defaultdict(list)
    for task_id, h in items.items():
        if h is not None:
            by_hash[h].append(task_id)
    return {h: sorted(ids) for h, ids in by_hash.items() if len(ids) >= 2}


def _finding_single_file_artifact(
    signatures: list[TaskSignature], *, attr: str, rule_prefix: str
) -> list[Finding]:
    findings: list[Finding] = []
    by_task = {sig.task_id: getattr(sig.artifact_hashes, attr) for sig in signatures}
    shape_by_task = {sig.task_id: sig.scenario_shape for sig in signatures}

    for h, task_ids in sorted(_clone_groups(by_task).items()):
        by_shape: dict[tuple[str, ...], list[str]] = defaultdict(list)
        for t in task_ids:
            by_shape[shape_by_task[t]].append(t)

        fail_members: set[str] = set()
        for shape, sub in sorted(by_shape.items()):
            if len(sub) >= 2:
                findings.append(
                    Finding(
                        level="FAIL",
                        rule=f"duplicate_{rule_prefix}_same_shape",
                        tasks=sorted(sub),
                        message=f"identical {rule_prefix} content, shared scenario_shape={shape!r}",
                    )
                )
                fail_members.update(sub)

        findings.append(
            Finding(
                level="INFO",
                rule=f"byte_clone_{rule_prefix}",
                tasks=task_ids,
                message=f"identical {rule_prefix} content across {len(task_ids)} tasks",
            )
        )
    return findings


def _finding_fixtures(signatures: list[TaskSignature]) -> list[Finding]:
    findings: list[Finding] = []
    vendor_by_task = {sig.task_id: sig.vendor for sig in signatures}

    # hash -> list of (task_id, filename)
    by_hash: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for sig in signatures:
        for filename, h in sig.artifact_hashes.fixtures.items():
            by_hash[h].append((sig.task_id, filename))

    for h, entries in sorted(by_hash.items()):
        task_ids = sorted({t for t, _f in entries})
        if len(task_ids) < 2:
            continue
        vendors = {vendor_by_task[t] for t in task_ids}
        if len(vendors) >= 2:
            findings.append(
                Finding(
                    level="FAIL",
                    rule="duplicate_fixture_cross_vendor",
                    tasks=task_ids,
                    message=f"identical fixture-file content across vendors={sorted(vendors)!r}",
                )
            )
        else:
            findings.append(
                Finding(
                    level="INFO",
                    rule="byte_clone_fixture",
                    tasks=task_ids,
                    message=f"identical fixture-file content across {len(task_ids)} tasks (same vendor)",
                )
            )
    return findings


def _finding_docs(signatures: list[TaskSignature]) -> list[Finding]:
    findings: list[Finding] = []
    # relpath -> hash -> [task_id, ...]
    by_relpath: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for sig in signatures:
        for relpath, h in sig.artifact_hashes.docs.items():
            by_relpath[relpath][h].append(sig.task_id)

    for relpath, by_hash in sorted(by_relpath.items()):
        for h, task_ids in sorted(by_hash.items()):
            if len(task_ids) >= 2:
                findings.append(
                    Finding(
                        level="INFO",
                        rule="byte_clone_docs",
                        tasks=sorted(task_ids),
                        message=f"identical docs/{relpath} content across {len(task_ids)} tasks",
                    )
                )
    return findings


def _finding_gate_topology(signatures: list[TaskSignature]) -> list[Finding]:
    findings: list[Finding] = []
    for sig in signatures:
        total_task_checks = sum(g.total_checks for g in sig.module_gates.values())
        if total_task_checks == 0:
            continue
        for module_name, gate in sorted(sig.module_gates.items()):
            if gate.checks_before_gate is None:
                continue
            share = gate.total_checks / total_task_checks
            if share > 0.30 and gate.checks_before_gate <= 2:
                findings.append(
                    Finding(
                        level="WARN",
                        rule="gate_topology_risk",
                        tasks=[sig.task_id],
                        message=(
                            f"{module_name} holds {gate.total_checks}/{total_task_checks} "
                            f"({share:.0%}) of this task's check calls with only "
                            f"{gate.checks_before_gate} recorded before its first "
                            "conditional return"
                        ),
                    )
                )
    return findings


@dataclasses.dataclass
class SuiteReport:
    findings: list[Finding]
    jaccard_pairs: list[tuple[str, str, float]]
    signatures: dict[str, TaskSignature]
    parse_errors: dict[str, list[str]]

    @property
    def fail_count(self) -> int:
        return sum(1 for f in self.findings if f.level == "FAIL")

    @property
    def warn_count(self) -> int:
        return sum(1 for f in self.findings if f.level == "WARN")

    @property
    def info_count(self) -> int:
        return sum(1 for f in self.findings if f.level == "INFO")

    def render(self) -> str:
        lines = [
            f"Suite uniqueness lint: {len(self.signatures)} task(s)",
            f"  FAIL={self.fail_count} WARN={self.warn_count} INFO={self.info_count}",
            "",
        ]
        for level in ("FAIL", "WARN", "INFO"):
            level_findings = [f for f in self.findings if f.level == level]
            if not level_findings:
                continue
            lines.append(f"-- {level} ({len(level_findings)}) --")
            for f in level_findings:
                lines.append("  " + f.render())
            lines.append("")

        lines.append(f"-- top {min(20, len(self.jaccard_pairs))} check-name Jaccard pairs --")
        for task_a, task_b, score in self.jaccard_pairs[:20]:
            lines.append(f"  {score:.3f}  {task_a}  {task_b}")
        lines.append("")

        if self.parse_errors:
            lines.append("-- parse errors (non-fatal) --")
            for task_id, errs in sorted(self.parse_errors.items()):
                for e in errs:
                    lines.append(f"  {task_id}: {e}")
            lines.append("")

        lines.append(
            f"Overall: {'FAIL' if self.fail_count else 'PASS'} (report-only unless --enforce)"
        )
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_count": len(self.signatures),
            "fail_count": self.fail_count,
            "warn_count": self.warn_count,
            "info_count": self.info_count,
            "findings": [f.to_dict() for f in self.findings],
            "jaccard_pairs_top20": [
                {"task_a": a, "task_b": b, "score": score}
                for a, b, score in self.jaccard_pairs[:20]
            ],
            "parse_errors": self.parse_errors,
        }


def lint_suite(
    tasks_dir: Path,
    *,
    mechanics_path: Path | None = None,
    compare_tasks_dir: Path | None = None,
    compare_label: str | None = None,
) -> SuiteReport:
    """Lint `tasks_dir`. If `compare_tasks_dir` is given, additionally check
    this suite against that one for cross-suite mechanic reuse (WARN); the
    other suite is NOT linted for its own internal findings."""
    mechanics = load_mechanics(mechanics_path)
    task_dirs = discover_tasks(tasks_dir)
    signatures = [build_task_signature(d) for d in task_dirs]

    parse_errors = {sig.task_id: sig.errors for sig in signatures if sig.errors}

    all_pairs = _all_pairs_jaccard(signatures)

    findings: list[Finding] = []
    findings.extend(_finding_mechanic(signatures, mechanics))
    findings.extend(_finding_missing_base_path(signatures))
    findings.extend(_finding_patch_applies(signatures))
    findings.extend(_finding_docker_copy_sources(signatures))
    findings.extend(_finding_vacuous_grading(signatures))
    findings.extend(_finding_scenario_shape(signatures))
    findings.extend(_finding_fault_scenario_profile(signatures))
    findings.extend(_finding_check_name_jaccard(all_pairs))
    findings.extend(
        _finding_single_file_artifact(signatures, attr="vendor_yaml", rule_prefix="vendor_yaml")
    )
    findings.extend(
        _finding_single_file_artifact(
            signatures, attr="mutations_yaml", rule_prefix="mutations_yaml"
        )
    )
    findings.extend(_finding_fixtures(signatures))
    findings.extend(_finding_docs(signatures))
    findings.extend(_finding_gate_topology(signatures))

    if compare_tasks_dir is not None:
        label = compare_label or Path(compare_tasks_dir).resolve().parent.name
        compare_signatures = [
            build_task_signature(d) for d in discover_tasks(Path(compare_tasks_dir))
        ]
        findings.extend(_finding_cross_suite_mechanic(signatures, compare_signatures, label))

    return SuiteReport(
        findings=findings,
        jaccard_pairs=all_pairs,
        signatures={sig.task_id: sig for sig in signatures},
        parse_errors=parse_errors,
    )


def write_json_report(report: SuiteReport, path: Path) -> None:
    Path(path).write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")

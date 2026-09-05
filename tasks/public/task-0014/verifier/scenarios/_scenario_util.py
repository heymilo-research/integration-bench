"""Shared helpers for the task-0014 (HireWire correction backlog under
FAULT_500_AFTER_COMMIT) scenarios.

The correction backlog (business rule, PROBLEM.md): every non-deleted
candidate whose ``stage`` is ``screening`` in the base seed (VENDOR_SEED=4000,
CHECKPOINT=0) -- a fixed, deterministic 26-candidate set (see
``scripts/generate_fixtures.py``, which derives it the same way the gold
connector does: page through ``GET /v1/candidates`` and filter).

Gold's write order (ascending candidate id; per candidate: POST the audit
event, then PATCH the stage) puts ``TARGET_CANDIDATE_ID`` -- the SECOND
backlog candidate -- at write-commit-index 3, which is exactly where
``docker-compose.yaml``'s default ``FAULT_500_AFTER_COMMIT=3`` lands: that
candidate's audit-event POST commits in full but the response lies with a
500.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from typing import Any

from bench.verifier.io import read_json_output

VENDOR = "hirewire"

# The full correction backlog at VENDOR_SEED=4000 / CHECKPOINT=0 -- every
# candidate whose seeded stage is `screening`, ascending by id. Pinned here
# (and re-derived independently by generate_fixtures.py against the live
# in-process vendor) so the ladder's L1 checks are exact regardless of any
# runtime timing.
ALL_TARGET_IDS: list[str] = [
    "cand_0019", "cand_0020", "cand_0023", "cand_0028", "cand_0030",
    "cand_0036", "cand_0040", "cand_0044", "cand_0046", "cand_0052",
    "cand_0053", "cand_0055", "cand_0056", "cand_0058", "cand_0064",
    "cand_0067", "cand_0079", "cand_0083", "cand_0084", "cand_0087",
    "cand_0104", "cand_0106", "cand_0110", "cand_0113", "cand_0114",
    "cand_0118",
]

# The 2nd backlog candidate in ascending order -- its audit-event POST is
# write-commit-index 3, which is exactly where the default
# FAULT_500_AFTER_COMMIT=3 lands (index 1 = cand_0019's event POST, index 2
# = cand_0019's stage PATCH, index 3 = cand_0020's event POST).
TARGET_CANDIDATE_ID = "cand_0020"

# retry_storm_immunity.py's intensified fault (4 consecutive qualifying
# commits lie in a row, write-commit indices 3-6). At gold's write order
# this reaches BOTH the 2nd (cand_0020) and 3rd (cand_0023) backlog
# candidates' writes.
STORM_FAULT_ENV = "3:4"
STORM_AFFECTED_CANDIDATE_IDS = ["cand_0020", "cand_0023"]

# docker-compose.yaml's default fault config (the single ambiguous commit at
# write-commit-index 3).
DEFAULT_FAULT_ENV = "3"

# Two writes (one audit event, one stage PATCH) per backlog candidate --
# the true, expected count of real server-side writes for a full pass, no
# matter how many faults fired or how many times any of them got retried.
EXPECTED_TOTAL_REAL_WRITES = 2 * len(ALL_TARGET_IDS)

_PATCH_PATH_RE = re.compile(r"^/v1/candidates/[^/]+$")
_EVENT_PATH_RE = re.compile(r"^/v1/candidates/[^/]+/events$")
LIST_PATH = "/v1/candidates"
READ_AFTER_WRITE_LAG_S = 20.0


# ---------------------------------------------------------------------------
# App output / fixtures
# ---------------------------------------------------------------------------


def load_fixture(ctx, name: str) -> Any:
    return json.loads((ctx.fixtures / name).read_text(encoding="utf-8"))


def read_output(ctx, name: str = "writeback_result.json") -> Any | None:
    """Read the connector's declared output (task.yaml ``outputs.files``).

    Goes through ``bench.verifier.io.read_json_output`` rather than a bare
    ``read_text``: the file is written INSIDE the app container onto a
    bind-mounted volume, and on macOS Docker Desktop (VirtioFS) it can take a
    moment to become visible/complete on the host after `docker compose run`
    returns. A direct read can therefore see "missing" or truncated JSON and
    mis-score a correct submission — a failure mode that does not exist for an
    in-process rig, where the same process writes the file. Short timeout: the
    poll cost is paid on every genuinely-absent output (the empty/stub floor
    probes read three times per grade), and the observed sync lag is
    sub-second because the writing container has already exited.
    """
    return read_json_output(ctx.output_dir / name, timeout_s=8.0)


def clear_output(ctx) -> None:
    try:
        (ctx.output_dir / "writeback_result.json").unlink()
    except OSError:
        pass


def run_correct(ctx) -> tuple[int, str, str]:
    return ctx.app.run(["correct"])


# ---------------------------------------------------------------------------
# Vendor lifecycle
# ---------------------------------------------------------------------------


def recreate_vendor(ctx, *, fault_env: str = DEFAULT_FAULT_ENV, checkpoint: int = 0):
    """Recreate the vendor at `checkpoint` with FAULT_500_AFTER_COMMIT set
    EXPLICITLY to `fault_env`.

    The compose env override lives on the ComposeStack for the whole verdict,
    not for one scenario: once retry_storm_immunity sets the intensified
    `3:4`, every LATER scenario inherits it unless it says otherwise. So each
    scenario states the fault config it wants instead of assuming
    docker-compose.yaml's default is still in force (which it is only for
    scenarios that run before the storm one).
    """
    handle = ctx.vendor(VENDOR)
    stack = handle._stack
    stack.vendor_envs.setdefault(handle._service, {})["FAULT_500_AFTER_COMMIT"] = fault_env
    handle.recreate(checkpoint=checkpoint)
    return handle


# ---------------------------------------------------------------------------
# Request-log classification
# ---------------------------------------------------------------------------


def _status_of(entry: dict[str, Any]) -> int:
    try:
        return int(entry.get("status", 200))
    except (TypeError, ValueError):
        return 200


def is_patch_call(entry: dict[str, Any]) -> bool:
    return entry.get("method") == "PATCH" and bool(_PATCH_PATH_RE.match(entry.get("path", "")))


def is_event_call(entry: dict[str, Any]) -> bool:
    return entry.get("method") == "POST" and bool(_EVENT_PATH_RE.match(entry.get("path", "")))


def write_calls(request_log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in request_log if is_patch_call(e) or is_event_call(e)]


def event_calls_for(request_log: list[dict[str, Any]], candidate_id: str) -> list[dict[str, Any]]:
    path = f"/v1/candidates/{candidate_id}/events"
    return [e for e in request_log if e.get("method") == "POST" and e.get("path") == path]


def patch_calls_for(request_log: list[dict[str, Any]], candidate_id: str) -> list[dict[str, Any]]:
    path = f"/v1/candidates/{candidate_id}"
    return [e for e in request_log if e.get("method") == "PATCH" and e.get("path") == path]


def faulted_write_calls(request_log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Write calls (PATCH candidate / POST event) that came back >=500 --
    the evidence that FAULT_500_AFTER_COMMIT actually fired this run."""
    return [e for e in write_calls(request_log) if _status_of(e) >= 500]


def candidate_list_reads(request_log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in request_log if e.get("method") == "GET" and e.get("path") == LIST_PATH]


def distinct_write_count(entries: list[dict[str, Any]]) -> int:
    """Count of logically DISTINCT write attempts among same-endpoint
    requests, ordered by log position. A request whose Idempotency-Key was
    already seen replays HireWire's stored TRUE result (the idempotency
    cache short-circuits before the write logic re-runs, LIES.md) and does
    NOT count as a new write; a request with no key, or a key never seen
    before, is a genuinely new write attempt -- exactly the signal a blind
    (unkeyed) retry trips."""
    seen_keys: set[str] = set()
    distinct = 0
    for entry in entries:
        key = entry.get("idempotency_key")
        if key and key in seen_keys:
            continue
        distinct += 1
        if key:
            seen_keys.add(key)
    return distinct


def retried_write_groups(request_log: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Groups of write requests (PATCH candidate / POST event) sharing an
    identical (method, path, body) that were sent more than once -- the
    evidence slice for same_key_retry_violations: without an actual retry,
    idempotency-on-retry conduct is unobservable."""
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for entry in write_calls(request_log):
        key = (entry.get("method"), entry.get("path"), json.dumps(entry.get("body"), sort_keys=True, default=str))
        groups.setdefault(key, []).append(entry)
    return [entries for entries in groups.values() if len(entries) >= 2]


def same_key_retry_violations(request_log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Stricter than "carries *a* key": every retry of the SAME logical
    write (same method+path+body, >=2 calls) must carry the literal SAME
    Idempotency-Key as that write's first attempt. A retry with no key, or
    with a DIFFERENT key than the first attempt, is a violation -- either
    one makes HireWire treat the retry as an independent write and risks
    (or, under FAULT_500_AFTER_COMMIT, guarantees) a real duplicate."""
    violations: list[dict[str, Any]] = []
    for group in retried_write_groups(request_log):
        ordered = sorted(group, key=lambda e: e.get("ts", 0))
        first_key = ordered[0].get("idempotency_key")
        for later in ordered[1:]:
            if not first_key or later.get("idempotency_key") != first_key:
                violations.append(later)
    return violations


def all_write_endpoint_groups(request_log: list[dict[str, Any]]) -> dict[tuple, list[dict[str, Any]]]:
    """Group write calls by (method, path) -- i.e. by logical write target
    (one specific candidate's event-POST endpoint, or one specific
    candidate's stage-PATCH endpoint)."""
    groups: dict[tuple, list[dict[str, Any]]] = {}
    for entry in write_calls(request_log):
        key = (entry.get("method"), entry.get("path"))
        groups.setdefault(key, []).append(entry)
    return groups


def total_distinct_write_count(request_log: list[dict[str, Any]], method: str | None = None) -> int:
    """Sum of distinct_write_count across every logical write endpoint
    touched in the log -- the true count of real server-side writes across
    the whole run. For this task's backlog, the true count is always
    exactly EXPECTED_TOTAL_REAL_WRITES (one event + one stage change per
    candidate); anything more means a blind/mismatched-key retry created at
    least one real duplicate somewhere -- not necessarily on the specific
    candidate(s) any other single check happens to be watching.

    ``method`` optionally restricts the sum to one write TYPE ("POST" for
    audit events, "PATCH" for stage changes) -- a duplicate event and a
    duplicate stage-PATCH are independent failure surfaces (an event is
    always a genuinely new row; a same-value stage PATCH repeated is
    invisible in the final GET-by-id value but still a real extra write),
    so callers that want each counted on its own use this."""
    total = 0
    for (call_method, _path), entries in all_write_endpoint_groups(request_log).items():
        if method is not None and call_method != method:
            continue
        ordered = sorted(entries, key=lambda e: e.get("ts", 0))
        total += distinct_write_count(ordered)
    return total


def list_reads_in_fault_window(
    request_log: list[dict[str, Any]], window_s: float = READ_AFTER_WRITE_LAG_S
) -> list[dict[str, Any]]:
    """Candidate-LIST reads whose timestamp falls within `window_s` seconds
    AFTER some faulted write's timestamp -- the exact window in which LIST
    is guaranteed stale (read_after_write_lag_s) and therefore useless (and
    dangerous, per LIES.md) as a confirmation signal for that write."""
    faulted = faulted_write_calls(request_log)
    if not faulted:
        return []
    lists = candidate_list_reads(request_log)
    hits: list[dict[str, Any]] = []
    for f in faulted:
        f_ts = f.get("ts", 0)
        for entry in lists:
            l_ts = entry.get("ts", 0)
            if f_ts <= l_ts < f_ts + window_s:
                hits.append(entry)
    return hits


# ---------------------------------------------------------------------------
# Verifier-side direct probe (independent of the connector under test)
# ---------------------------------------------------------------------------


def vendor_get_candidate(ctx, candidate_id: str) -> tuple[int, dict[str, Any]]:
    """A GET-by-id issued directly by the verifier, NOT routed through the
    connector under test -- server-side ground truth, independent of
    whatever the connector's own output claims (docs/writeback.md: GET-by-id
    is immediately consistent, unlike LIST)."""
    base = ctx.vendor(VENDOR).base_url
    api_key = ctx.secrets.get("HW_API_KEY", "hw-test-api-key")
    req = urllib.request.Request(
        f"{base}/v1/candidates/{candidate_id}",
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read()
            return resp.getcode(), (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, (json.loads(raw) if raw else {})
        except json.JSONDecodeError:
            return exc.code, {}

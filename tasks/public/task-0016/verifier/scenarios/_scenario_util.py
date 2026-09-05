"""Shared helpers for the task-0016 (HireWire writeback + polling) scenarios.

The connector is driven entirely through one-shot subcommands (``push`` /
``poll`` / ``dump``) via ``ctx.app.run([...])``. Each scenario recreates the
vendor at a known checkpoint (which also truncates the vendor's request log and
resets its in-memory writeback store) and inspects the resulting output files
plus the vendor request log.
"""

from __future__ import annotations

import json
from typing import Any

from bench.verifier.io import read_json_output

VENDOR = "hirewire"

# The staged batch is baked into the repo; these are the client_refs it declares
# and which of them is expected to fail validation (missing event_type -> 422).
EVENT_REFS = ["evt-1", "evt-2", "evt-3"]
OK_REFS = ["evt-1", "evt-2"]
FAILED_REFS = ["evt-3"]

# candidate_ids the successful staged events target (PATCH + POST land on these).
OK_CANDIDATE_IDS = {"evt-1": "cand_0001", "evt-2": "cand_0007"}
FAILED_CANDIDATE_ID = "cand_0013"


def load_fixture(ctx, name: str) -> Any:
    return json.loads((ctx.fixtures / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Output readers
# ---------------------------------------------------------------------------

def read_writeback_result(ctx) -> dict[str, Any] | None:
    return read_json_output(ctx.output_dir / "writeback_result.json", timeout_s=15.0)


def read_candidates(ctx) -> list[dict[str, Any]] | None:
    return read_json_output(ctx.output_dir / "candidates.json", timeout_s=15.0)


def clear_outputs(ctx) -> None:
    """Remove any stale outputs (and the poll watermark) so a scenario never
    reads a previous run's file or resumes a previous run's watermark."""
    for name in ("writeback_result.json", "candidates.json", ".poll_state.json"):
        try:
            (ctx.output_dir / name).unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Request-log analysis
# ---------------------------------------------------------------------------

def _is_2xx(code: Any) -> bool:
    try:
        return 200 <= int(code) < 300
    except (TypeError, ValueError):
        return False


def event_posts(request_log: list[dict[str, Any]], candidate_id: str | None = None,
                accepted_only: bool = False) -> list[dict[str, Any]]:
    """POST /v1/candidates/{id}/events, optionally filtered to one candidate
    and/or to accepted (2xx) attempts only."""
    out = []
    for e in request_log:
        if e.get("method") != "POST":
            continue
        path = e.get("path", "")
        if not (path.startswith("/v1/candidates/") and path.endswith("/events")):
            continue
        if candidate_id is not None and path != f"/v1/candidates/{candidate_id}/events":
            continue
        if accepted_only and not _is_2xx(e.get("status")):
            continue
        out.append(e)
    return out


def candidate_patches(request_log: list[dict[str, Any]], candidate_id: str | None = None,
                      accepted_only: bool = False) -> list[dict[str, Any]]:
    """PATCH /v1/candidates/{id} (not the /events sub-path)."""
    out = []
    for e in request_log:
        if e.get("method") != "PATCH":
            continue
        path = e.get("path", "")
        if not path.startswith("/v1/candidates/") or path.endswith("/events"):
            continue
        if candidate_id is not None and path != f"/v1/candidates/{candidate_id}":
            continue
        if accepted_only and not _is_2xx(e.get("status")):
            continue
        out.append(e)
    return out


def candidate_list_reads(request_log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """GET /v1/candidates (the LIST endpoint, no id) — this is the LAGGING
    read-after-write path; confirming a fresh write through it is the trap."""
    return [
        e for e in request_log
        if e.get("method") == "GET" and e.get("path") == "/v1/candidates"
    ]


def candidate_get_by_id_reads(request_log: list[dict[str, Any]],
                              candidate_id: str | None = None) -> list[dict[str, Any]]:
    """GET /v1/candidates/{id} — the immediately-consistent read-by-id path."""
    out = []
    for e in request_log:
        if e.get("method") != "GET":
            continue
        path = e.get("path", "")
        if not path.startswith("/v1/candidates/") or path.endswith("/events"):
            continue
        if path == "/v1/candidates":
            continue
        if candidate_id is not None and path != f"/v1/candidates/{candidate_id}":
            continue
        out.append(e)
    return out


def event_field_diff(result: dict[str, Any] | None,
                     fixture: dict[str, Any]) -> list[dict[str, Any]]:
    """Per-client_ref, per-field comparison of writeback_result.json against the
    answer key.

    The targeted replacement for the old `result == fixture` blob compares
    (`server_state_matches_fixture` / `retry_did_not_create_new_records`): it
    reports WHICH ref and WHICH field disagree instead of a single opaque
    verdict, and it is order-insensitive (a correct connector is free to emit
    its events in any order).
    """
    want = {e.get("client_ref"): e for e in fixture.get("events", [])}
    got = {e.get("client_ref"): e for e in (result or {}).get("events", [])}
    diffs: list[dict[str, Any]] = []
    for ref in EVENT_REFS:
        w, g = want.get(ref) or {}, got.get(ref)
        if g is None:
            diffs.append({"client_ref": ref, "field": "<entire event>", "got": None})
            continue
        for key in sorted(set(w) | set(g)):
            if w.get(key) != g.get(key):
                diffs.append({"client_ref": ref, "field": key,
                              "want": w.get(key), "got": g.get(key)})
    for ref in got:
        if ref not in want:
            diffs.append({"client_ref": ref, "field": "<unexpected event>", "want": None})
    return diffs


def row_diff(store: list[dict[str, Any]] | None,
             want: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-source_id, per-field comparison of candidates.json against the answer
    key -- the targeted replacement for the old `store == cp5` blob compare."""
    got_by_id = {r.get("source_id"): r for r in (store or [])}
    want_by_id = {r.get("source_id"): r for r in want}
    diffs: list[dict[str, Any]] = []
    for sid in sorted(set(want_by_id) | set(got_by_id)):
        w, g = want_by_id.get(sid), got_by_id.get(sid)
        if g is None:
            diffs.append({"source_id": sid, "field": "<missing row>"})
            continue
        if w is None:
            diffs.append({"source_id": sid, "field": "<unexpected row>"})
            continue
        for key in sorted(set(w) | set(g)):
            if w.get(key) != g.get(key):
                diffs.append({"source_id": sid, "field": key,
                              "want": w.get(key), "got": g.get(key)})
    return diffs


def diff_detail(label: str, diffs: list[dict[str, Any]], limit: int = 4) -> str:
    if not diffs:
        return ""
    shown = json.dumps(diffs[:limit], sort_keys=True, default=str)
    more = f" (+{len(diffs) - limit} more)" if len(diffs) > limit else ""
    return f"{label}: {len(diffs)} field diff(s): {shown}{more}"


def candidate_list_pages(request_log: list[dict[str, Any]],
                         with_modified_since: bool | None = None) -> list[dict[str, Any]]:
    """GET /v1/candidates list pages, optionally filtered by whether the request
    carried a modified_since watermark."""
    out = []
    for e in request_log:
        if e.get("method") != "GET" or e.get("path") != "/v1/candidates":
            continue
        has_ms = "modified_since" in (e.get("query") or {})
        if with_modified_since is None or has_ms == with_modified_since:
            out.append(e)
    return out

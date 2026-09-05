"""Shared helpers for the task-0041 scenarios.

Paygrade's connector is a pure file-and-env, one-shot CLI: `sync` writes
`employees.json` / `assignments.json` / `.sync_state.json` straight to
`OUTPUT_DIR` (bind-mounted), and `writeback` writes `writeback_result.json`.
There is no database to reset between scenarios and no long-lived listener,
so scenario isolation is just "use a fresh OUTPUT_DIR subdirectory" (wired via
the `OUTPUT_DIR` env in docker-compose.yaml, one per scenario) plus recreating
the vendor at the checkpoint the scenario needs.
"""

from __future__ import annotations

import json
from typing import Any


def load_fixture(ctx, name: str) -> Any:
    return json.loads((ctx.fixtures / name).read_text(encoding="utf-8"))


def read_output(ctx, name: str) -> Any | None:
    path = ctx.output_dir / name
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def clear_output(ctx) -> None:
    """Remove every file this connector can produce so a scenario never reads
    a stale snapshot (or stale watermark) left by an earlier scenario run."""
    for name in ("employees.json", "assignments.json", "writeback_result.json", ".sync_state.json"):
        try:
            (ctx.output_dir / name).unlink()
        except OSError:
            pass


def index_by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {r["source_id"]: r for r in rows}


def row_diff(rows: list[dict[str, Any]] | None,
             want: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-source_id, per-field comparison against an answer key.

    Restores the signal of the deleted blob compares
    `initial_sync_employees_match_fixture`,
    `initial_sync_assignments_match_fixture`,
    `tombstone_sweep_employees_match_fixture` and
    `tombstone_sweep_assignments_match_fixture`.

    The surviving checks in those two scenarios are unusually granular already
    (row counts, per-entity create/update assertions, no-premature-tombstones,
    deletes-marked-with-data-retained), so what the deletion actually cost is
    narrower than in most tasks but still real: every field of every row NOT
    named by one of those per-entity checks. Paygrade's whole mechanic is a
    tombstone-only delete that RETAINS the row's data, so "the right rows exist
    with the right tombstone flags" and "their retained data is correct" are two
    different claims, and only the first survived.
    """
    if rows is None:
        return [{"source_id": "<no output>", "field": "<missing or unreadable>"}]
    got_by_id = {r.get("source_id"): r for r in rows}
    want_by_id = {r.get("source_id"): r for r in want}
    diffs: list[dict[str, Any]] = []
    for sid in sorted(set(want_by_id) | set(got_by_id), key=str):
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


def ref_diff(items: list[dict[str, Any]] | None,
             want: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-client_ref, per-field comparison of writeback_result.json's writes.

    Restores the signal of the deleted `writeback_result_matches_fixture`. The
    surviving checks grade which refs are reported ok vs failed and some
    request-log facts, but not the recorded CONTENT of each write outcome — and
    this vendor answers 200 for a failed write, so the recorded outcome is the
    only place the distinction lives.
    """
    key = "client_ref"
    if items is None:
        return [{key: "<no output>", "field": "<missing or unreadable>"}]
    got_by = {i.get(key): i for i in items}
    want_by = {i.get(key): i for i in want}
    diffs: list[dict[str, Any]] = []
    for k in sorted(set(want_by) | set(got_by), key=str):
        w, g = want_by.get(k), got_by.get(k)
        if g is None:
            diffs.append({key: k, "field": "<missing item>"})
            continue
        if w is None:
            diffs.append({key: k, "field": "<unexpected item>"})
            continue
        for field in sorted(set(w) | set(g)):
            if w.get(field) != g.get(field):
                diffs.append({key: k, "field": field,
                              "want": w.get(field), "got": g.get(field)})
    return diffs


def diff_detail(label: str, items: list | None, want: list,
                diffs: list, limit: int = 3) -> str:
    n = "none" if items is None else len(items)
    if not diffs:
        return f"{label}: {n} row(s), every field matches the answer key"
    shown = json.dumps(diffs[:limit], sort_keys=True, default=str)
    more = f" (+{len(diffs) - limit} more)" if len(diffs) > limit else ""
    return f"{label}: rows={n} expected={len(want)}; {len(diffs)} field diff(s): {shown}{more}"

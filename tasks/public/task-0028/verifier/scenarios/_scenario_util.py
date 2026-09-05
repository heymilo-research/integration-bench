"""Shared helpers for the task-0028 scenarios.

Pure polling task, file-backed store: the connector is driven entirely
through the one-shot `sync` subcommand via ``ctx.app.run(["sync"])``. Each
`sync` is a full, stateless snapshot (CrewCall has no incremental surface),
so output files are simply re-read after each run.

2026-08 re-ladder (docs/specs/rework/task-0028.spec.md): every helper below
is written to be called UNCONDITIONALLY by its scenario -- each one guards
its own inputs against missing/None data rather than relying on the caller
to early-return before it. This is what lets `backfill_under_faults` and
`reconverge_under_faults` bank partial credit for a connector that survives
the 500s but mishandles the rate limiter (or vice versa), instead of one
early return on `outputs is None` gating everything downstream of it.
"""

from __future__ import annotations

import json
from typing import Any

from bench.verifier.io import read_json_output

_FILES = {
    "worker": "workers.json",
    "gig": "gigs.json",
    "assignment": "assignments.json",
}

_DEFAULT_RETRY_AFTER_S = 6


def read_outputs(ctx) -> dict[str, list[dict[str, Any]] | None]:
    """Read all three output files after a `sync` run.

    Per-entity independence (spec fix 2): an entity whose file is missing or
    unreadable maps to None in the returned dict -- it no longer collapses
    the WHOLE result to a single None the way the pre-2026-08 version did.
    Callers must grade each entity's own checks off its own None-or-rows
    value; a bad file for one entity must never zero out the other two.
    """
    return {
        entity: read_json_output(ctx.output_dir / filename, timeout_s=15.0)
        for entity, filename in _FILES.items()
    }


def load_fixture(ctx, name: str) -> list[dict[str, Any]]:
    return json.loads((ctx.fixtures / name).read_text(encoding="utf-8"))


def no_duplicate_ids(rows: list[dict[str, Any]]) -> bool:
    ids = [r["source_id"] for r in rows]
    return len(ids) == len(set(ids))


def worker_list_requests(request_log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        e for e in request_log
        if e.get("method") == "GET" and e.get("path") == "/v1/workers"
    ]


def full_pass_starts(entries: list[dict[str, Any]]) -> int:
    """Count how many times the worker-list crawl restarted at offset=0 (a
    new full pass), in request order."""
    sorted_entries = sorted(entries, key=lambda e: e.get("ts", 0))
    return sum(1 for e in sorted_entries if str((e.get("query") or {}).get("offset")) == "0")


def _offset(entry: dict[str, Any]) -> int:
    try:
        return int((entry.get("query") or {}).get("offset") or 0)
    except (TypeError, ValueError):
        return -1


def _status(entry: dict[str, Any]) -> int:
    try:
        return int(entry.get("status") or 0)
    except (TypeError, ValueError):
        return 0


# --------------------------------------------------------------------- rung 2


def check_entity_correctness(
    ctx,
    entity: str,
    rows: list[dict[str, Any]] | None,
    fixture: list[dict[str, Any]],
    expected_count: int,
) -> None:
    """Rung 2 -- per-entity data correctness, partial credit (spec fix 2).

    A missing/unreadable output file for THIS entity fails only this
    entity's own two checks here; it never touches the other entities'
    checks, nor any other rung (log forensics, exactly-once, conduct).
    """
    # `{entity}_matches_fixture` replaced by `{entity}_rows_exact`: a per-row,
    # per-field diff instead of `rows == fixture`. Order-insensitive, which
    # matters here for the same reason as task-0026 -- the worker roster
    # RE-SORTS during the crawl, so a converged connector's emission order is
    # not predictable from the fixture's.
    #
    # Values split by entity, with literals in each branch. The stacked faults
    # (drift + 5xx on worker page 1 + the 429 limiter) all land on the WORKER
    # crawl; gigs and assignments are undrifted, unfaulted, single-page reads.
    # So worker correctness is the summit this task is named for (+2, mandatory)
    # and the other two are ordinary supporting correctness (+1).
    diffs = row_diff(rows, fixture)
    detail = diff_detail(entity, rows, fixture, diffs)
    count_ok = rows is not None and len(rows) == expected_count
    count_detail = (
        f"{entity} output file missing/unreadable" if rows is None
        else f"{entity} rows={len(rows)} expected={expected_count}"
    )
    if entity == "worker":
        ctx.check(f"{entity}_rows_exact", not diffs, detail,
            pass_value=2, fail_value=0, mandatory=True,
        )
        ctx.check(f"{entity}_expected_count", count_ok, count_detail,
            pass_value=2, fail_value=0, mandatory=False,
        )
    else:
        ctx.check(f"{entity}_rows_exact", not diffs, detail,
            pass_value=1, fail_value=0, mandatory=False,
        )
        ctx.check(f"{entity}_expected_count", count_ok, count_detail,
            pass_value=1, fail_value=0, mandatory=False,
        )


# --------------------------------------------------------------------- rung 4


def check_entity_no_duplicate_ids(ctx, entity: str, rows: list[dict[str, Any]] | None) -> None:
    """Rung 4 -- per-entity dup-id check, split out from raw matches_fixture
    (spec fix 4): a connector with the right SET of ids but a stale field
    value is a different failure mode than one with duplicate ids, and
    should score differently."""
    dup_ok = rows is not None and no_duplicate_ids(rows)
    dup_detail = (
        f"{entity} output file missing/unreadable" if rows is None
        else f"{entity} has duplicate source_id(s)"
    )
    # Same worker/undrifted split as rung 2: a duplicated worker id is the
    # direct symptom of a crawl that never reconverged through the drift.
    if entity == "worker":
        ctx.check(f"{entity}_no_duplicate_ids", dup_ok, dup_detail,
            pass_value=2, fail_value=0, mandatory=False,
        )
    else:
        ctx.check(f"{entity}_no_duplicate_ids", dup_ok, dup_detail,
            pass_value=1, fail_value=0, mandatory=False,
        )


def check_exactly_once(
    ctx,
    outputs: dict[str, list[dict[str, Any]] | None],
    fixtures: dict[str, list[dict[str, Any]]],
) -> None:
    """Rung 4 (L3) -- exactly-once across all three entities: no duplicate
    ids AND counts equal fixtures. Kept distinct from
    `check_entity_correctness`'s raw matches_fixture (spec fix 4). Guards
    each entity's rows against None so a missing file for one entity is
    recorded as a detail line, not a crash."""
    dup_detail = []
    counts_ok = True
    for entity, fixture in fixtures.items():
        rows = outputs[entity]
        if rows is None:
            counts_ok = False
            dup_detail.append(f"{entity} output file missing/unreadable")
            continue
        ids = [r["source_id"] for r in rows]
        if len(ids) != len(set(ids)):
            seen, dups = set(), set()
            for i in ids:
                (dups if i in seen else seen).add(i)
            dup_detail.append(f"{entity} dups={sorted(dups)[:5]}")
        if len(rows) != len(fixture):
            counts_ok = False
    ctx.check("exactly_once",
        not dup_detail and counts_ok,
        "; ".join(dup_detail) if dup_detail else f"counts_ok={counts_ok}",
        pass_value=2,
        fail_value=0,
        mandatory=False,
    )


# --------------------------------------------------------------------- rung 3


def check_log_forensics(ctx, request_log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rung 3 -- pure request-log forensics (spec fix 3): `resume_not_restart`
    and `retry_after_honored` read ONLY `vendor.request_log()`, never the
    output files, so they must run unconditionally -- whether or not `sync`
    ever produced a single readable output file. Returns the sorted
    worker-list request log so callers that need it again (e.g.
    reconverge's `full_pass_starts` convergence check) don't recompute it.
    """
    worker_reqs = sorted(worker_list_requests(request_log), key=lambda e: e.get("ts", 0))

    # (a) retry-in-place, scoped WITHIN a pass: after the first 5xx at the
    #     faulted offset X, the very next request to X (chronologically)
    #     must be the recovering 200 -- no request at any offset < X may be
    #     observed in between. Deliberately scoped to the narrow
    #     fault-to-recovery window, not the whole run: once offset X has
    #     recovered, later full-pass re-crawls that revisit offset 0 (the
    #     documented dedupe-and-reconverge recipe -- see docs/pagination.md)
    #     are expected and must NOT be flagged.
    # Derive the injected page from observed traffic.  Page ordinal is a
    # vendor-fixture detail; offset depends on the connector's contract-valid
    # page size and must never be hardcoded by the verifier.
    first_fault = next((e for e in worker_reqs if _status(e) >= 500), None)
    first_fault_ts = first_fault.get("ts", 0) if first_fault is not None else None
    fault_page_offset = _offset(first_fault) if first_fault is not None else None
    recovery_ts = None
    interleaved_earlier: list[int] = []
    if first_fault_ts is not None:
        after_fault = [e for e in worker_reqs if e.get("ts", 0) > first_fault_ts]
        for e in after_fault:
            off = _offset(e)
            if off == fault_page_offset and _status(e) == 200:
                recovery_ts = e.get("ts", 0)
                break
            if 0 <= off < fault_page_offset:
                interleaved_earlier.append(off)
    retried_ok = recovery_ts is not None
    ctx.check("resume_not_restart",
        first_fault_ts is not None and retried_ok and not interleaved_earlier,
        f"fault_seen={first_fault_ts is not None} retried_ok={retried_ok} "
        f"fault_offset={fault_page_offset} "
        f"earlier_offsets_before_recovery={sorted(set(interleaved_earlier))[:5]}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # (b) retry_after_honored: if a 429 fired anywhere in the run, the next
    #     request to that same path must land >= 6s later (the advertised
    #     Retry-After). If no 429 fired (unlucky request-count timing), the
    #     check trivially passes -- nothing to honor.
    all_get = sorted(
        (e for e in request_log if e.get("method") == "GET"),
        key=lambda e: e.get("ts", 0),
    )
    violations = []
    saw_429 = False
    for i, entry in enumerate(all_get):
        if _status(entry) != 429:
            continue
        saw_429 = True
        retry_after = entry.get("retry_after") or _DEFAULT_RETRY_AFTER_S
        path = entry.get("path")
        nxt = next(
            (e for e in all_get[i + 1:] if e.get("path") == path),
            None,
        )
        if nxt is not None:
            gap = nxt.get("ts", 0) - entry.get("ts", 0)
            if gap < float(retry_after):
                violations.append(f"{path} gap={gap:.2f}s retry_after={retry_after}")
    # Evidence gate (mirrors builtin_l2's traffic-conditional rule, which
    # this check is a scenario-local clone of): the compose healthcheck polls
    # ``GET /`` for the container's entire lifetime, so gate on the
    # submission's own data-plane GETs — a do-nothing run makes none and
    # must not bank "honored Retry-After" on silence.
    data_plane_get = [e for e in all_get if e.get("path") not in ("/", "")]
    # NOT `retry_after_honored`: that name belongs to builtin_l2's own SOFT
    # check on the same signal, which this function is a scenario-local clone
    # of. builtin_l2 records it with the legacy soft defaults (0, 0) and this
    # one with (0, -1), so the same name carried two different scorings and
    # which instance won was arbitrary -- caught by check_probe_bar's
    # "same check name recorded with DIFFERENT scoring" assertion. Renamed
    # rather than realigned, because the two assertions really are different:
    # builtin_l2's is generic conduct across the whole log, this one is scoped
    # to the faulted crawl and gated on the submission's own data-plane GETs.
    if data_plane_get:
        ctx.check("no_retry_after_violation_under_faults",
            not violations,
            f"saw_429={saw_429} violations={violations[:5]}",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )

    return worker_reqs


def row_diff(rows: list[dict[str, Any]] | None,
             want: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-source_id, per-field comparison against an answer key.

    Replaces the whole-document compares `worker_matches_fixture`,
    `gig_matches_fixture` and `assignment_matches_fixture` (`rows == fixture`)
    with `{entity}_rows_exact`.
    """
    if rows is None:
        return [{"source_id": "<no output>", "field": "<file missing>"}]
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


def diff_detail(label: str, rows: list[dict[str, Any]] | None,
                want: list[dict[str, Any]], diffs: list[dict[str, Any]],
                limit: int = 3) -> str:
    n = "none" if rows is None else len(rows)
    if not diffs:
        return f"{label}: {n} row(s), every field matches the answer key"
    shown = json.dumps(diffs[:limit], sort_keys=True, default=str)
    more = f" (+{len(diffs) - limit} more)" if len(diffs) > limit else ""
    return f"{label}: rows={n} expected={len(want)}; {len(diffs)} field diff(s): {shown}{more}"

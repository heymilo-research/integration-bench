"""Shared helpers for the task-0012 (TalentLoop summit: webhooks + polling +
writeback under combined faults) scenarios. Builds on the same serve-as-a-
service / drain-webhooks / fault-env-isolation patterns used by
task-0023/0024/0025's `_scenario_util.py`, extended for:

- all 4 entities (candidate/job/application/note) in `dump_store`/
  `clear_outputs`, plus the writeback result file.
- the full combined-fault vocabulary this task needs
  (`FAULT_DROP_EVENT_IDS`, `FAULT_DUP_STORM(+_N)`, `FAULT_5XX_ON_PAGE`,
  `FAULT_TOKEN_EXPIRY_MIDRUN`).
- request_log/token_log helpers for the resume_not_restart /
  token_reauth_transparent L3 checks.
"""

from __future__ import annotations

import json
import time
from typing import Any

from bench.verifier.io import read_json_output

APP_SERVICE = "app"
VENDOR = "talentloop"
_DRAIN_TIMEOUT_S = 15.0
_DRAIN_POLL_S = 0.25
# A truly dropped event is filtered out of the dispatch plan before any
# attempt is ever made (vendors/talentloop/src/talentloop/webhooks.py
# build_delivery_plan), so its absence is immediate and stable -- this window
# just guards against a flaky false-negative from checking too early.
_ABSENCE_SETTLE_S = 12.0

_OUTPUT_FILES = ("candidates.json", "jobs.json", "applications.json", "notes.json")
_DUMP_KINDS = ("candidate", "job", "application", "note")

# NOTE: FAULT_DUP_STORM_N is deliberately NOT a member of this tuple. It is a
# MAGNITUDE parameter the vendor reads as ``int(os.environ.get(..., "4"))``
# (talentloop/webhooks.py's build_delivery_plan) -- truly ABSENT falls back to
# its documented default of 4, but forcing it to the empty string (as a naive
# "clear every fault key" pass would) makes that same ``int("")`` call raise
# ValueError during the vendor's FastAPI ``lifespan()`` startup, crashing the
# container outright (exit code 3) on EVERY ``set_fault_env`` call, not just
# ones that legitimately want a non-default N. Only the true fault
# switches/selectors get force-cleared here.
_FAULT_KEYS = (
    "FAULT_DROP_EVENT_IDS",
    "FAULT_DUP_STORM",
    "FAULT_5XX_ON_PAGE",
    "FAULT_TOKEN_EXPIRY_MIDRUN",
)


def _stack(ctx):
    return ctx.app._stack


def set_fault_env(ctx, **faults: str) -> None:
    """Set exactly the given fault env var(s) on the vendor, clearing every
    OTHER known fault key first so each scenario's signal stays isolated (no
    leftover fault from a previously-run scenario sharing this same compose
    stack). Takes effect on the NEXT ``handle.recreate()``.

    ``FAULT_DUP_STORM_N`` is left untouched unless explicitly passed in
    ``faults`` -- see the module-level note on ``_FAULT_KEYS`` for why
    force-clearing it to ``""`` crashes the vendor at boot.
    """
    vendor = ctx.vendor(VENDOR)
    env = vendor._stack.vendor_env
    for key in _FAULT_KEYS:
        env[key] = ""
    env.update(faults)


def reset_store(ctx) -> None:
    """Drop the canonical sqlite DB so each scenario starts empty.

    Scenarios share one DB file on the ``canonical-data`` volume for the whole
    grade; without this, tombstones/watermarks from an earlier scenario leak.
    """
    from bench.canonical_sqlite import reset_canonical_on_stack

    reset_canonical_on_stack(_stack(ctx))



def clear_outputs(ctx) -> None:
    for name in (*_OUTPUT_FILES, "writeback_result.json"):
        try:
            (ctx.output_dir / name).unlink()
        except OSError:
            pass


def dump_store(ctx) -> dict[str, list[dict[str, Any]]] | None:
    clear_outputs(ctx)
    code, _stdout, _stderr = ctx.app.run(["dump"])
    if code != 0:
        return None
    out: dict[str, list[dict[str, Any]]] = {}
    for kind, fname in zip(_DUMP_KINDS, _OUTPUT_FILES):
        rows = read_json_output(ctx.output_dir / fname, timeout_s=15.0)
        if rows is None:
            return None
        out[kind] = rows
    return out


def load_fixture(ctx, name: str) -> Any:
    return json.loads((ctx.fixtures / name).read_text(encoding="utf-8"))


def serve_start(ctx) -> None:
    stack = _stack(ctx)
    stack.up(service=APP_SERVICE, force_recreate=True)
    _wait_listener(stack)


def serve_stop(ctx) -> None:
    stack = _stack(ctx)
    try:
        stack.stop_service(APP_SERVICE)
    except Exception:
        pass


def _wait_listener(stack, timeout_s: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        probe = stack.exec(
            "vendor", "python", "-c",
            "import urllib.request,sys;"
            "sys.exit(0 if urllib.request.urlopen('http://connector:4000/',timeout=2).status==200 else 1)",
            check=False,
        )
        if probe.returncode == 0:
            return
        time.sleep(0.5)


def _is_2xx(code: Any) -> bool:
    try:
        return 200 <= int(code) < 300
    except (TypeError, ValueError):
        return False


def drain_webhooks(
    ctx,
    *,
    expect_events: set[str],
    timeout_s: float = _DRAIN_TIMEOUT_S,
) -> bool:
    """Poll the vendor delivery log until every id in ``expect_events`` has a
    2xx ack from the live listener. Returns True on success."""
    handle = ctx.vendor(VENDOR)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        deliveries = handle.webhook_deliveries()
        acked = {d.get("event_id") for d in deliveries if _is_2xx(d.get("status_code"))}
        if expect_events.issubset(acked):
            time.sleep(0.3)
            return True
        time.sleep(_DRAIN_POLL_S)
    return False


def drain_checkpoint_events(
    ctx,
    steps: list[tuple[int, set[str]]],
    *,
    timeout_s: float = _DRAIN_TIMEOUT_S,
) -> tuple[bool, list[dict[str, Any]]]:
    """Recreate the vendor through each ``(checkpoint, expected_event_ids)``
    step IN ORDER, draining after each individual recreate.

    The dispatcher only ever queues events for the single half-open window
    ``(checkpoint-1, checkpoint]`` on a given boot (never cumulative), and
    every boot also truncates ``webhook_deliveries.jsonl`` to empty. A single
    jump straight to the final checkpoint therefore only ever delivers that
    LAST mutation's event; collecting events for mutations spread across
    several checkpoints requires one recreate+drain cycle per checkpoint.
    Concatenates each step's delivery log (read right after that step's
    drain, before the next recreate wipes it).
    """
    handle = ctx.vendor(VENDOR)
    all_deliveries: list[dict[str, Any]] = []
    all_ok = True
    for cp, expect_events in steps:
        handle.recreate(checkpoint=cp)
        ok = drain_webhooks(ctx, expect_events=expect_events, timeout_s=timeout_s)
        all_ok = all_ok and ok
        all_deliveries.extend(handle.webhook_deliveries())
    return all_ok, all_deliveries


def assert_never_delivered(ctx, event_id: str, settle_s: float = _ABSENCE_SETTLE_S) -> bool:
    """Watch the delivery log for ``settle_s`` and return True iff ``event_id``
    NEVER appears -- i.e. the drop fault genuinely suppressed the delivery
    attempt entirely (not "attempted and failed", not "delivered late")."""
    handle = ctx.vendor(VENDOR)
    deadline = time.monotonic() + settle_s
    while time.monotonic() < deadline:
        deliveries = handle.webhook_deliveries()
        if any(d.get("event_id") == event_id for d in deliveries):
            return False
        time.sleep(1.0)
    deliveries = handle.webhook_deliveries()
    return not any(d.get("event_id") == event_id for d in deliveries)


_PLURAL = {"candidate": "candidates", "job": "jobs", "application": "applications", "note": "notes"}


def get_by_id_reads(request_log: list[dict[str, Any]], *, kind: str, entity_id: str) -> list[dict[str, Any]]:
    """Successful (2xx) ``GET /{kind}s/{entity_id}`` calls in the request log
    -- used to prove exactly-once dedup: a broken dedup implementation
    re-fetches on every duplicate delivery, so this count directly reflects
    how many times the connector actually re-processed the same event.

    Deliberately excludes a 401 attempt: the vendor's in-memory auth state
    (``talentloop/auth.py``'s ``_ISSUED``) is wiped on every boot, including a
    mid-scenario ``handle.recreate()`` between checkpoints, so a long-lived
    ``serve`` process's cached token from before that recreate is guaranteed
    stale. The client's transparent re-auth-and-retry (per ``client.py``) on
    the first post-recreate call produces one 401 attempt followed by one 200
    retry for the SAME logical fetch -- correct auth behavior, not evidence of
    a second, dedup-driven re-fetch. Counting the 401 here would make this
    check unpassable by any correct connector.
    """
    path = f"/{_PLURAL[kind]}/{entity_id}"
    return [
        e for e in request_log
        if e.get("method") == "GET" and e.get("path") == path and 200 <= status_of(e) < 300
    ]


def status_of(entry: dict[str, Any]) -> int:
    try:
        return int(entry.get("status", 0))
    except (TypeError, ValueError):
        return 0


def cursor_of(entry: dict[str, Any]) -> str:
    return str((entry.get("query") or {}).get("cursor") or "")


# ---------------------------------------------------------------------------
# Per-record graders (2026-08-07, per-test-scoring migration)
#
# Replace this task's fourteen `dumped[kind] == fixture` whole-store compares
# (backfill_/reconcile_/multidrop_/freshness_/session_ x 4 entity kinds) plus the
# two writeback document compares. Each voted once for everything it covered and
# could only report "candidate: rows=250 fixture=250" — the least informative
# summary available for a task whose whole subject is a SINGLE record that should
# have been tombstoned and was not.
#
# None of these calls ctx.check: they return (ok, detail) so every scored value
# stays a literal at its call site in the scenario, which is what lets
# tools/check_migration.py audit the tree statically.
# ---------------------------------------------------------------------------


def row_count_ok(got, want) -> tuple[bool, str]:
    rows = got if isinstance(got, list) else None
    n = len(rows) if rows is not None else None
    return n == len(want), (
        f"rows={n if n is not None else 'missing/unreadable'} want={len(want)}"
    )


def store_row_diff(got, want) -> list[str]:
    """Per-row, per-field differences between a dumped store and its answer key.

    `is_deleted` is compared first and named explicitly because it is the field
    this whole task turns on: a missed tombstone leaves the row count untouched
    and the row present, which is precisely what a row-count check cannot see.
    """
    gi = {r.get("source_id"): r for r in (got or []) if isinstance(r, dict)}
    wi = {r.get("source_id"): r for r in want if isinstance(r, dict)}
    out: list[str] = []
    for sid, wrow in wi.items():
        grow = gi.get(sid)
        if grow is None:
            out.append(f"{sid}: missing")
            continue
        if bool(grow.get("is_deleted")) != bool(wrow.get("is_deleted")):
            out.append(
                f"{sid}.is_deleted: got={grow.get('is_deleted')!r} "
                f"want={wrow.get('is_deleted')!r}"
            )
        gdata, wdata = grow.get("data") or {}, wrow.get("data") or {}
        if not isinstance(gdata, dict):
            out.append(f"{sid}.data: got={gdata!r} want an object")
            continue
        if not isinstance(wdata, dict):
            out.append(f"{sid}.data: invalid answer-key value {wdata!r}")
            continue
        for field, wval in wdata.items():
            if gdata.get(field) != wval:
                out.append(f"{sid}.{field}: got={gdata.get(field)!r} want={wval!r}")
    for sid in sorted(gi.keys() - wi.keys(), key=str):
        out.append(f"{sid}: not in the answer key")
    return out


def diff_detail(kind: str, diffs: list[str]) -> str:
    return f"{kind}: {len(diffs)} field difference(s): {diffs[:4] or 'none'}"


def writeback_event_diff(result, fixture) -> list[str]:
    """Per-client_ref, per-field differences in writeback_result.json."""
    result_obj = result if isinstance(result, dict) else {}
    fixture_obj = fixture if isinstance(fixture, dict) else {}
    got = {
        e.get("client_ref"): e
        for e in (result_obj.get("events") or [])
        if isinstance(e, dict)
    }
    want = {
        e.get("client_ref"): e
        for e in (fixture_obj.get("events") or [])
        if isinstance(e, dict)
    }
    out: list[str] = []
    for ref, wev in want.items():
        gev = got.get(ref)
        if gev is None:
            out.append(f"{ref}: missing from events")
            continue
        for key in ("kind", "ok"):
            if gev.get(key) != wev.get(key):
                out.append(f"{ref}.{key}: got={gev.get(key)!r} want={wev.get(key)!r}")
        for section in ("record", "error"):
            gsec, wsec = gev.get(section) or {}, wev.get(section) or {}
            if not isinstance(gsec, dict):
                out.append(f"{ref}.{section}: got={gsec!r} want an object")
                continue
            if not isinstance(wsec, dict):
                out.append(f"{ref}.{section}: invalid answer-key value {wsec!r}")
                continue
            for field, wval in wsec.items():
                if gsec.get(field) != wval:
                    out.append(
                        f"{ref}.{section}.{field}: got={gsec.get(field)!r} want={wval!r}"
                    )
    for ref in sorted(got.keys() - want.keys(), key=str):
        out.append(f"{ref}: not in the answer key")
    return out


def writeback_record_ids(result) -> dict:
    """{client_ref: server-assigned id} for the successful events."""
    result_obj = result if isinstance(result, dict) else {}
    return {
        e.get("client_ref"): (
            (e.get("record") or {}).get("id")
            if isinstance(e.get("record") or {}, dict)
            else None
        )
        for e in (result_obj.get("events") or [])
        if isinstance(e, dict) and e.get("ok") is True
    }

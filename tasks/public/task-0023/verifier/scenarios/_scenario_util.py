"""Shared helpers for the task-0023 (TalentLoop webhooks+polling deletes)
scenarios.

These wrap the harness's ComposeStack/AppHandle/VendorHandle so each scenario
reads as a short sequence of intent-level steps. Nothing here mutates the
harness -- it only uses the stack objects the harness hands the scenario.

Key mechanic: ``talentloop_deletes serve`` is a long-lived HTTP listener the
vendor must reach at ``http://connector:4000``. A ``docker compose run``
container does NOT get a service network alias, so we drive serve by bringing
the app *service* up (``docker compose up -d app``) -- which DOES carry the
`connector` alias declared in docker-compose.yaml -- and stopping it
afterwards. One-shot subcommands (backfill / poll / dump) still go through
``ctx.app.run([...])``.

TalentLoop's built-in dispatcher (vendors/talentloop/src/talentloop/webhooks.py)
logs delivery attempts with BOTH ``status_code`` and ``response_code`` (the
latter is what bench.verifier.builtin_l2's generic hard gates read) plus
``skew_s`` -- unlike talentforge's dispatcher, so builtin_l2's webhook hard
gates are fully load-bearing here. This module's own drain/inspection helpers
still read ``status_code`` directly for the scenario-local L3 assertions.
"""

from __future__ import annotations

import time
from typing import Any

from bench.verifier.io import read_json_output

APP_SERVICE = "app"
VENDOR = "talentloop"
# Generous drain window so a slow container start under load never times out a
# legitimately-delivered event (the vendor dispatcher retries even longer).
_DRAIN_TIMEOUT_S = 15.0
_DRAIN_POLL_S = 0.25


def _stack(ctx):
    return ctx.app._stack


# ---------------------------------------------------------------------------
# Store isolation
# ---------------------------------------------------------------------------

def reset_store(ctx) -> None:
    """Drop the canonical sqlite DB so each scenario starts empty.

    Scenarios share one DB file on the ``canonical-data`` volume for the whole
    grade; without this, tombstones/watermarks from an earlier scenario leak.
    """
    from bench.canonical_sqlite import reset_canonical_on_stack

    reset_canonical_on_stack(_stack(ctx))


# ---------------------------------------------------------------------------
# Output inspection
# ---------------------------------------------------------------------------

def clear_outputs(ctx) -> None:
    for name in ("candidates.json", "applications.json"):
        try:
            (ctx.output_dir / name).unlink()
        except OSError:
            pass


def dump_store(ctx) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    """Snapshot the canonical store to output/*.json and read it back."""
    clear_outputs(ctx)
    code, _stdout, _stderr = ctx.app.run(["dump"])
    if code != 0:
        return None
    candidates = read_json_output(ctx.output_dir / "candidates.json", timeout_s=15.0)
    applications = read_json_output(ctx.output_dir / "applications.json", timeout_s=15.0)
    if candidates is None or applications is None:
        return None
    return candidates, applications


def load_fixture(ctx, name: str) -> Any:
    import json

    return json.loads((ctx.fixtures / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Serve lifecycle (service up/stop so the vendor can reach http://connector:4000)
# ---------------------------------------------------------------------------

def serve_start(ctx) -> None:
    """Bring the app serve listener up as a real service (gets `connector` alias)."""
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
    """Wait until the serve container answers on its port from inside the vendor
    (via the `connector` network alias the vendor's webhook target uses)."""
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


# ---------------------------------------------------------------------------
# Webhook draining
# ---------------------------------------------------------------------------

def _is_2xx(code: Any) -> bool:
    try:
        return 200 <= int(code) < 300
    except (TypeError, ValueError):
        return False


def _rejected(code: Any) -> bool:
    if code is None:
        return False
    try:
        return not (200 <= int(code) < 300)
    except (TypeError, ValueError):
        return False


def drain_webhooks(
    ctx,
    *,
    expect_events: set[str],
    expect_tampered: bool = False,
    timeout_s: float = _DRAIN_TIMEOUT_S,
) -> bool:
    """Poll the vendor delivery log until every id in ``expect_events`` has a
    2xx ack from the live listener (and, if ``expect_tampered``, at least one
    tampered delivery has been observed rejected non-2xx)."""
    handle = ctx.vendor(VENDOR)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        deliveries = handle.webhook_deliveries()
        acked = {
            d.get("event_id")
            for d in deliveries
            if _is_2xx(d.get("status_code")) and not d.get("tampered")
        }
        events_ok = expect_events.issubset(acked)
        tamper_ok = True
        if expect_tampered:
            tamper_ok = any(
                d.get("tampered") and _rejected(d.get("status_code")) for d in deliveries
            )
        if events_ok and tamper_ok:
            # Small settle so any in-flight attempt is also logged.
            time.sleep(0.3)
            return True
        time.sleep(_DRAIN_POLL_S)
    return False


# ---------------------------------------------------------------------------
# Request-log inspection
# ---------------------------------------------------------------------------

def candidate_get_by_id_reads(request_log: list[dict[str, Any]], *, candidate_id: str) -> list[dict[str, Any]]:
    path = f"/candidates/{candidate_id}"
    return [e for e in request_log if e.get("method") == "GET" and e.get("path") == path]


def candidate_list_reads(request_log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in request_log if e.get("method") == "GET" and e.get("path") == "/candidates"]


# ---------------------------------------------------------------------------
# Answer-key comparison
# ---------------------------------------------------------------------------

def row_diff(store: list[dict[str, Any]] | None,
             want: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-source_id, per-field comparison against an answer key.

    The targeted replacement for this task's nine deleted
    `{phase}_{entity}_match_fixture` blob compares. Their deletion left all
    three scenarios grading exactly ONE fact — `cand_0007.is_deleted` — so
    every other candidate row, every application row, and every field other
    than the tombstone flag went unchecked in every phase.

    Order-insensitive by source_id: dump order is not part of the contract.
    """
    if store is None:
        return [{"source_id": "<no output>", "field": "<store unreadable>"}]
    got_by_id = {r.get("source_id"): r for r in store}
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


def diff_detail(label: str, store: list[dict[str, Any]] | None,
                want: list[dict[str, Any]], diffs: list[dict[str, Any]],
                limit: int = 3) -> str:
    n = "none" if store is None else len(store)
    if not diffs:
        return f"{label}: {n} row(s), every field matches the answer key"
    import json as _json
    shown = _json.dumps(diffs[:limit], sort_keys=True, default=str)
    more = f" (+{len(diffs) - limit} more)" if len(diffs) > limit else ""
    return f"{label}: rows={n} expected={len(want)}; {len(diffs)} field diff(s): {shown}{more}"

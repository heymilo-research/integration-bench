"""Shared helpers for the task-0011 (TalentForge webhooks+writeback) scenarios.

These wrap the harness's ComposeStack/AppHandle/VendorHandle so each scenario
reads as a short sequence of intent-level steps. Nothing here mutates the
harness -- it only uses the stack objects the harness hands the scenario.

Key mechanic: ``talentforge_hooks serve`` is a long-lived HTTP listener the
vendor must reach at ``http://connector:4000``. A ``docker compose run``
container does NOT get a service network alias, so we drive serve by bringing
the app *service* up (``docker compose up -d app``) -- which DOES carry the
`connector` alias declared in docker-compose.yaml -- and stopping it
afterwards. One-shot subcommands (backfill / push / dump) still go through
``ctx.app.run([...])``.

NOTE on the vendor's webhook delivery log field names: talentforge's built-in
dispatcher (vendors/talentforge/src/talentforge/webhooks.py) logs
``{event_id, event, entity_id, attempt, status_code, duplicate, tampered}`` --
notably NOT ``response_code``/``skew_s`` (which some other vendors' dispatcher
wrappers emit for bench.verifier.builtin_l2's generic hard-gate field
assumptions). This module's helpers, and the scenarios' own L3 checks, read
``status_code`` directly rather than relying solely on builtin_l2 for
tamper/skew verdicts.
"""

from __future__ import annotations

import json
import time
from typing import Any

from bench.verifier.io import read_json_output

APP_SERVICE = "app"
VENDOR = "talentforge"
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
    for name in ("candidates.json", "applications.json", "writeback_result.json"):
        try:
            (ctx.output_dir / name).unlink()
        except OSError:
            pass


def dump_store(ctx) -> tuple[list[dict[str, Any]], list[dict[str, Any]]] | None:
    """Snapshot the canonical store to output/*.json and read it back."""
    for name in ("candidates.json", "applications.json"):
        try:
            (ctx.output_dir / name).unlink()
        except OSError:
            pass
    code, _stdout, _stderr = ctx.app.run(["dump"])
    if code != 0:
        return None
    candidates = read_json_output(ctx.output_dir / "candidates.json", timeout_s=15.0)
    applications = read_json_output(ctx.output_dir / "applications.json", timeout_s=15.0)
    if candidates is None or applications is None:
        return None
    return candidates, applications


def read_writeback_result(ctx) -> dict[str, Any] | None:
    return read_json_output(ctx.output_dir / "writeback_result.json", timeout_s=15.0)


def load_fixture(ctx, name: str) -> Any:
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
            "talentforge", "python", "-c",
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
    tampered delivery has been observed rejected non-2xx). Returns True on
    success. Reads ``status_code`` (talentforge's actual delivery-log field --
    see the module docstring)."""
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


def drain_checkpoint_events(
    ctx,
    steps: list[tuple[int, set[str]]],
    *,
    expect_tampered_at: set[int] | None = None,
    timeout_s: float = _DRAIN_TIMEOUT_S,
) -> tuple[bool, list[dict[str, Any]]]:
    """Recreate the vendor through each ``(checkpoint, expected_event_ids)``
    step IN ORDER, draining after each individual recreate.

    Mechanic this exists to handle correctly: the vendor's dispatcher only
    ever queues events for the single half-open window ``(checkpoint-1,
    checkpoint]`` on a given boot (see talentforge/webhooks.py
    ``build_delivery_plan``) -- it is NOT cumulative -- and every boot also
    truncates ``webhook_deliveries.jsonl`` to empty (see main.py's lifespan).
    So collecting events for N mutations spread across N checkpoints requires
    N separate recreate+drain cycles, not one recreate straight to the final
    checkpoint. This helper does that and concatenates each step's delivery
    log (read right after that step's drain, before the next recreate wipes
    it) so callers can still inspect the full cross-checkpoint history
    afterwards (e.g. to assert a tampered delivery was seen and rejected).
    """
    handle = ctx.vendor(VENDOR)
    all_deliveries: list[dict[str, Any]] = []
    all_ok = True
    for cp, expect_events in steps:
        handle.recreate(checkpoint=cp)
        want_tamper = expect_tampered_at is None or cp in expect_tampered_at
        ok = drain_webhooks(
            ctx, expect_events=expect_events, expect_tampered=want_tamper, timeout_s=timeout_s
        )
        all_ok = all_ok and ok
        all_deliveries.extend(handle.webhook_deliveries())
    return all_ok, all_deliveries


# ---------------------------------------------------------------------------
# Request-log inspection (writeback confirmation patterns)
# ---------------------------------------------------------------------------

def _accepted(entry: dict[str, Any]) -> bool:
    try:
        return 200 <= int(entry.get("status", 0)) < 300
    except (TypeError, ValueError):
        return False


def candidate_get_by_id_reads(request_log: list[dict[str, Any]], *, candidate_id: str) -> list[dict[str, Any]]:
    path = f"/rest/candidates/{candidate_id}"
    return [e for e in request_log if e.get("method") == "GET" and e.get("path") == path]


def candidate_list_reads(request_log: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in request_log if e.get("method") == "GET" and e.get("path") == "/rest/candidates"]


def candidate_patches(request_log: list[dict[str, Any]], *, candidate_id: str, accepted_only: bool = False) -> list[dict[str, Any]]:
    path = f"/rest/candidates/{candidate_id}"
    out = [e for e in request_log if e.get("method") == "PATCH" and e.get("path") == path]
    return [e for e in out if _accepted(e)] if accepted_only else out


def candidate_creates(request_log: list[dict[str, Any]], *, accepted_only: bool = False) -> list[dict[str, Any]]:
    out = [e for e in request_log if e.get("method") == "POST" and e.get("path") == "/rest/candidates"]
    return [e for e in out if _accepted(e)] if accepted_only else out


def note_posts(request_log: list[dict[str, Any]], *, candidate_id: str, accepted_only: bool = False) -> list[dict[str, Any]]:
    path = f"/rest/candidates/{candidate_id}/notes"
    out = [e for e in request_log if e.get("method") == "POST" and e.get("path") == path]
    return [e for e in out if _accepted(e)] if accepted_only else out


# ---------------------------------------------------------------------------
# Per-record graders (2026-08-07, per-test-scoring migration)
#
# Replace the six `X == fixture` whole-document compares this task used to run
# (server_state / retry_did_not_create_new_records / bridge_result /
# tamper_candidates / tamper_applications / backfill_*). Each voted once for
# everything it covered, so a connector that lost 50 rows and one that dropped a
# single field scored the same zero and the detail string could say only how many
# rows came back.
#
# None of these calls ctx.check: they return (ok, detail) so every scored value
# stays a literal at its call site in the scenario, which is what lets
# tools/check_migration.py audit the tree statically.
# ---------------------------------------------------------------------------


def store_row_diff(got: Any, want: list[dict[str, Any]]) -> list[str]:
    """Per-row, per-field differences between a dumped store and its answer key."""
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
        for field, wval in wdata.items():
            if gdata.get(field) != wval:
                out.append(f"{sid}.{field}: got={gdata.get(field)!r} want={wval!r}")
    for sid in sorted(gi.keys() - wi.keys(), key=str):
        out.append(f"{sid}: not in the answer key")
    return out


def row_count_ok(got: Any, want: list) -> tuple[bool, str]:
    rows = got if isinstance(got, list) else None
    n = len(rows) if rows is not None else None
    return n == len(want), (
        f"rows={n if n is not None else 'missing/unreadable'} want={len(want)}"
    )


def diff_detail(diffs: list[str]) -> str:
    return f"{len(diffs)} field difference(s): {diffs[:4] or 'none'}"


def writeback_event_diff(result: Any, fixture: dict[str, Any]) -> list[str]:
    """Per-client_ref, per-field differences in writeback_result.json.

    The `record` bodies carry the server-assigned ids and values, which is the
    signal the deleted `server_state_matches_fixture` compare actually held.
    """
    got = {
        e.get("client_ref"): e
        for e in ((result or {}).get("events") or [])
        if isinstance(e, dict)
    }
    want = {
        e.get("client_ref"): e
        for e in (fixture.get("events") or [])
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
        grec, wrec = gev.get("record") or {}, wev.get("record") or {}
        for field, wval in wrec.items():
            if grec.get(field) != wval:
                out.append(f"{ref}.record.{field}: got={grec.get(field)!r} want={wval!r}")
        gerr, werr = gev.get("error") or {}, wev.get("error") or {}
        for field, wval in werr.items():
            if gerr.get(field) != wval:
                out.append(f"{ref}.error.{field}: got={gerr.get(field)!r} want={wval!r}")
    for ref in sorted(got.keys() - want.keys(), key=str):
        out.append(f"{ref}: not in the answer key")
    return out


def writeback_record_ids(result: Any) -> dict[str, Any]:
    """{client_ref: server-assigned id} — for comparing one push against another.

    A retry must re-attach to the records the first push created rather than
    minting new ones. Comparing the two pushes' id maps states that directly;
    comparing the second push to a fixture is a proxy that also fails for
    differences having nothing to do with idempotency.
    """
    return {
        e.get("client_ref"): (e.get("record") or {}).get("id")
        for e in ((result or {}).get("events") or [])
        if isinstance(e, dict) and e.get("ok") is True
    }


def bridge_report_diff(result: Any, fixture: dict[str, Any]) -> list[str]:
    """Per-candidate differences in bridge_result.json's provisioned/skipped lists."""
    out: list[str] = []
    for section in ("provisioned", "skipped"):
        got = {
            r.get("candidate_id"): r
            for r in ((result or {}).get(section) or [])
            if isinstance(r, dict)
        }
        want = {
            r.get("candidate_id"): r
            for r in (fixture.get(section) or [])
            if isinstance(r, dict)
        }
        for cid, wrow in want.items():
            grow = got.get(cid)
            if grow is None:
                out.append(f"{section}[{cid}]: missing")
                continue
            if wrow.get("reason") is not None and grow.get("reason") != wrow.get("reason"):
                out.append(
                    f"{section}[{cid}].reason: got={grow.get('reason')!r} "
                    f"want={wrow.get('reason')!r}"
                )
            gpkt, wpkt = grow.get("packet") or {}, wrow.get("packet") or {}
            for field, wval in wpkt.items():
                if gpkt.get(field) != wval:
                    out.append(
                        f"{section}[{cid}].packet.{field}: got={gpkt.get(field)!r} "
                        f"want={wval!r}"
                    )
        for cid in sorted(got.keys() - want.keys(), key=str):
            out.append(f"{section}[{cid}]: not in the answer key")
    return out

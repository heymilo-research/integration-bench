"""Shared helpers for the task-0027 (Interviewly booby-trapped-starter fix)
scenarios.

Same `serve`/`connector` alias mechanic and cumulative-dispatcher shape as the
sibling interviewly tasks. ``inject_delivery`` sends one extra, freshly-signed
(in-skew) webhook delivery straight at the connector's listener (executed
from inside the `vendor` container, which shares the docker network and can
resolve the `connector` alias -- the same reachability path `_wait_listener`
already uses).

This exists because neither of this task's two planted bugs is reachable from
the vendor's own delivery stream alone:

  - `FAULT_OOO_BURST` reorders the SAME handful of seeded, single-event-per-
    entity mutations; every stale/resent delivery Interviewly seeds is
    correctly signed over a STALE timestamp and is skew-rejected before
    dedupe is ever consulted (docs/webhooks.md), so it can never exercise a
    dedupe-capacity bug either. Testing capacity needs synthetic, freshly-
    signed, DISTINCT event_ids -- exactly the load a real burst produces.
  - No entity in the seeded mutation timeline ever receives more than one
    genuine event, so there is no naturally-occurring out-of-order run for
    the same entity to test the ordering gate against. That has to be
    scripted.

Both injections use the vendor's own real seed ids so the only synthesized
thing is the delivery repetition/timing, matching how a real burst or a real
reordering would actually reach the connector.

What the scenarios GRADE is the pair of declared output artifacts -- the
canonical store and ``event_journal.json`` (one entry per event the connector
applied, in apply order, per record). The journal is what makes a pre-filter
decision observable in the output at all: a double-apply is a duplicate entry,
a stale apply is a regressive entry, and a dropped event is a missing entry.
Request-log GET counts are deliberately NOT graded here -- an implementation
that reaches the right journal and the right store has done the job however it
chose to arrange its fetches.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any

from bench.verifier.io import read_json_output

APP_SERVICE = "app"
VENDOR = "interviewly"
_DRAIN_TIMEOUT_S = 15.0
_DRAIN_POLL_S = 0.25


def _stack(ctx):
    return ctx.app._stack


def _sign(secret: str, timestamp: str, raw_body: bytes) -> str:
    return hmac.new(
        secret.encode("utf-8"), (timestamp + ".").encode("utf-8") + raw_body, hashlib.sha256
    ).hexdigest()


def inject_delivery(
    ctx,
    *,
    event_id: str,
    event: str,
    entity_id: str,
    occurred_at: str,
    timeout_s: float = 10.0,
) -> int | None:
    """POST one extra, freshly-signed (in-skew) delivery to the connector's
    listener from inside the `vendor` container. Returns the HTTP status
    code observed, or None if the request itself failed."""
    stack = _stack(ctx)
    secret = ctx.secrets.get("IV_WEBHOOK_SECRET", "")
    payload = {
        "event_id": event_id,
        "event": event,
        "occurred_at": occurred_at,
        "data": {"id": entity_id},
    }
    raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ts = str(int(time.time()))
    signature = _sign(secret, ts, raw_body)
    script = (
        "import urllib.request,sys;"
        f"req=urllib.request.Request('http://connector:4000/webhooks/interviewly',"
        f"data={raw_body!r},method='POST',"
        f"headers={{'Content-Type':'application/json','X-IV-Signature':{signature!r},"
        f"'X-IV-Timestamp':{ts!r}}});"
        "\n"
        "try:\n"
        "    resp=urllib.request.urlopen(req,timeout=8)\n"
        "    print(resp.getcode())\n"
        "except urllib.error.HTTPError as e:\n"
        "    print(e.code)\n"
        "except Exception as e:\n"
        "    print('ERR', e)\n"
    )
    result = stack.exec("vendor", "python3", "-c", script, check=False)
    out = (result.stdout or "").strip()
    try:
        return int(out.splitlines()[-1])
    except (ValueError, IndexError):
        return None


def inject_run(ctx, deliveries: list[dict[str, str]], *, settle_s: float = 0.4) -> list[int | None]:
    """Deliver a SCRIPTED, ordered run of events one at a time, pausing between
    them so arrival order is exactly the list order (never a race). Each dict
    is ``{event_id, event, entity_id, occurred_at}``."""
    statuses: list[int | None] = []
    for d in deliveries:
        statuses.append(inject_delivery(ctx, **d))
        time.sleep(settle_s)
    return statuses


def flood_decoy_ids(ctx, *, count: int, entity_id: str, id_prefix: str) -> None:
    """Inject `count` distinct, fresh, correctly-signed no-op deliveries
    targeting a decoy entity id that doesn't exist in the seed data (a 404 on
    fetch, so it never touches real canonical rows and never journals an
    apply) -- pure dedupe-capacity pressure. See the module docstring for why
    this is required to exercise a fixed-size dedupe structure at all."""
    for i in range(1, count + 1):
        inject_delivery(
            ctx,
            event_id=f"{id_prefix}{i:05d}",
            event="interview.updated",
            entity_id=entity_id,
            occurred_at="2026-03-14T11:00:05Z",
        )


def read_output(ctx, filename: str) -> Any | None:
    return read_json_output(ctx.output_dir / filename, timeout_s=10.0)


def read_journal(ctx) -> dict[str, list[dict[str, Any]]]:
    """The connector's applied-event journal, or {} if it never wrote one."""
    journal = read_json_output(ctx.output_dir / "event_journal.json", timeout_s=10.0)
    return journal if isinstance(journal, dict) else {}


def journal_for(ctx, source_id: str) -> list[dict[str, Any]]:
    entries = read_journal(ctx).get(source_id)
    return entries if isinstance(entries, list) else []


def load_fixture(ctx, name: str) -> list[dict[str, Any]]:
    return json.loads((ctx.fixtures / name).read_text(encoding="utf-8"))


def fixture_row(ctx, name: str, source_id: str) -> dict[str, Any] | None:
    for row in load_fixture(ctx, name):
        if row["source_id"] == source_id:
            return row
    return None


def rows_by_id(rows: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    return {r["source_id"]: r for r in (rows or []) if isinstance(r, dict) and "source_id" in r}


def set_faults(ctx, *, ooo_burst: bool = False, replay_storm: bool = False, tamper: bool = False) -> None:
    vendor = ctx.vendor(VENDOR)
    vendor._stack.vendor_env["FAULT_OOO_BURST"] = "1" if ooo_burst else "0"
    vendor._stack.vendor_env["FAULT_REPLAY_STORM"] = "1" if replay_storm else "0"
    vendor._stack.vendor_env["TAMPER_INJECT"] = "1" if tamper else "0"


def reset_connector_state(ctx) -> None:
    """Clear the connector's persisted canonical store, journal and state.json
    so each scenario starts from a clean slate regardless of what a previous
    scenario left behind in the shared OUTPUT_DIR volume (the durable
    bookkeeping this task is about is, by design, meant to survive a restart --
    it must NOT survive into an unrelated scenario's measurements).

    Done on the host side of the bind mount rather than with `compose exec`:
    between scenarios the app container is STOPPED (`serve_stop`), and exec
    against a stopped container fails, which would silently leave the previous
    scenario's store and processed-event set in place."""
    for path in sorted(Path(ctx.output_dir).glob("*.json")):
        path.unlink(missing_ok=True)


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


def _wait_listener(stack, timeout_s: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        probe = stack.exec(
            "vendor",
            "python3",
            "-c",
            "import urllib.request,sys;"
            "sys.exit(0 if urllib.request.urlopen('http://connector:4000/',timeout=2).status==200 else 1)",
            check=False,
        )
        if probe.returncode == 0:
            return True
        time.sleep(0.5)
    return False


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


def tamper_rejected(ctx) -> bool:
    """True iff the vendor logged at least one tamper-injected delivery and the
    listener answered every one of them non-2xx."""
    deliveries = ctx.vendor(VENDOR).webhook_deliveries()
    tampered = [d for d in deliveries if d.get("tampered")]
    accepted = [d for d in tampered if _is_2xx(d.get("status_code"))]
    return bool(tampered) and not accepted


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
    success."""
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
            # Extra settle time: bursts keep delivering trailing noise well
            # after the real events land.
            time.sleep(3.0)
            return True
        time.sleep(_DRAIN_POLL_S)
    return False


def wait_for_request_log_quiet(ctx, *, quiet_for_s: float, timeout_s: float) -> None:
    """Block until the vendor's request log stops growing for `quiet_for_s`
    seconds straight (or `timeout_s` total elapses) -- used so the
    dispatcher's own trailing retries never overlap a synthetic injection
    measured immediately afterward."""
    vendor = ctx.vendor(VENDOR)
    deadline = time.monotonic() + timeout_s
    last_len = len(vendor.request_log())
    quiet_since = time.monotonic()
    while time.monotonic() < deadline:
        time.sleep(1.0)
        cur_len = len(vendor.request_log())
        if cur_len != last_len:
            last_len = cur_len
            quiet_since = time.monotonic()
        elif time.monotonic() - quiet_since >= quiet_for_s:
            return


def row_diff(rows: list[dict[str, Any]] | None,
             want: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per-source_id, per-field comparison against an expected table.

    The targeted replacement for the `got != expected` whole-table compares
    behind `store_matches_upstream_after_ooo_run` and
    `store_matches_upstream_under_burst`. Those rolled three tables into a
    single bool whose detail was only `rows=N expected=M` — useless for this
    task in particular, where the failure is almost always a WATERMARK on one
    row (`updated_at` regressed to a later-arriving older event) at an
    unchanged row count.
    """
    if rows is None:
        return [{"source_id": "<no output>", "field": "<unreadable>"}]
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


def store_diffs(ctx, expected_by_table: dict[str, list[dict[str, Any]]]) -> list[str]:
    """Run `row_diff` over every canonical table, returning one detail string
    per table that disagrees with its expected rows."""
    out: list[str] = []
    for table, expected in expected_by_table.items():
        got = read_output(ctx, f"{table}.json")
        diffs = row_diff(got, expected)
        if diffs:
            shown = json.dumps(diffs[:3], sort_keys=True, default=str)
            more = f" (+{len(diffs) - 3} more)" if len(diffs) > 3 else ""
            out.append(
                f"{table}: rows={len(got or [])} expected={len(expected)}; "
                f"{len(diffs)} field diff(s): {shown}{more}"
            )
    return out

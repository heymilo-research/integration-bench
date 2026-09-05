"""Shared helpers for the task-0038 (TalentLoop ack-with-body + dead-letter
queue) scenarios. Builds on the serve-as-a-service / drain-webhooks / fault-
env-isolation patterns used by the rest of the talentloop family
(task-0012/0023/0024's `_scenario_util.py`), extended for:

- ack-aware draining: under `TL_ACK_REQUIRED=1` a delivery counts as landed
  only when the vendor's own delivery log records `acked: true` -- a bare
  2xx `status_code` is NOT sufficient (that is precisely the "delivered but
  unacked" failure mode this task's mechanic tests for).
- direct, verifier-owned HTTP calls to the vendor's dead-letter-queue
  endpoints (`GET/POST/DELETE /v1/dead_letters*`) -- the only way to prove
  `dlq_drained_empty` (spec rung 5) is an *authoritative* fact about the
  vendor's own state, not merely what the connector's canonical store claims.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from bench.verifier.io import read_json_output

APP_SERVICE = "app"
VENDOR = "talentloop"
# Short relative to the rest of the talentloop family (150s default there):
# under TL_ACK_REQUIRED=1 the retry cadence is a FIXED 0.5s dispatch-cycle
# interval, not wall-clock backoff, so a correct connector acks within ~1s
# regardless of this ceiling. Keeping it short bounds how expensive it is to
# prove a BROKEN connector never acks (HARDENING-PATTERNS.md P11: "webhook
# gauntlets cost up to 2.6h -- keep drain legs minimal").
_DRAIN_TIMEOUT_S = 15.0
_DRAIN_POLL_S = 0.25

_OUTPUT_NAMES = ("candidates.json", "jobs.json", "applications.json", "notes.json")

# The only fault knob this task's scenarios flip between runs. TL_ACK_REQUIRED
# and TL_DELIVERY_MAX_ATTEMPTS are baseline env on the `vendor` service in
# docker-compose.yaml (this task's feature is always on, not a per-scenario
# toggle) -- only the drop fault differs between the two scenarios.
_FAULT_KEYS = ("FAULT_DROP_EVENT_IDS",)


def _stack(ctx):
    return ctx.app._stack


def set_fault_env(ctx, **faults: str) -> None:
    """Set exactly the given fault env var(s) on the vendor, clearing every
    OTHER known fault key first so each scenario's signal stays isolated.
    Takes effect on the NEXT ``handle.recreate()``."""
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
    for name in _OUTPUT_NAMES:
        try:
            (ctx.output_dir / name).unlink()
        except OSError:
            pass


def dump_store(ctx) -> tuple[list, list, list, list] | None:
    """Snapshot the canonical store to output/*.json and read it back as
    ``(candidates, jobs, applications, notes)``."""
    clear_outputs(ctx)
    code, _stdout, _stderr = ctx.app.run(["dump"])
    if code != 0:
        return None
    results = []
    for name in _OUTPUT_NAMES:
        data = read_json_output(ctx.output_dir / name, timeout_s=15.0)
        if data is None:
            return None
        results.append(data)
    return tuple(results)


def load_fixture(ctx, name: str) -> Any:
    return json.loads((ctx.fixtures / name).read_text(encoding="utf-8"))


def serve_start(ctx) -> None:
    """Bring the webhook listener up and wait until it is reachable.

    MUST be called before any ``handle.recreate()`` whose checkpoint is
    expected to deliver a webhook: under TL_ACK_REQUIRED=1 the vendor's
    retry cadence is a FIXED, fast dispatch-cycle interval (0.5s) rather than
    the legacy exponential backoff -- a listener that isn't already up and
    accepting connections by the vendor's FIRST attempt can burn through all
    of TL_DELIVERY_MAX_ATTEMPTS on pure container-startup timing and
    dead-letter an event that was never actually the point of the test.
    Starting the listener first (and leaving it running across every
    `handle.recreate()` in a step list) avoids that race entirely.
    """
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
    """Poll the vendor delivery log until every id in ``expect_events`` has
    landed. Under TL_ACK_REQUIRED=1, "landed" means the log's own `acked`
    field is true for a non-duplicate, non-tampered attempt of that event --
    a bare 2xx `status_code` is NOT sufficient (that is exactly the
    delivered-but-unacked failure mode). Returns True on success."""
    handle = ctx.vendor(VENDOR)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        deliveries = handle.webhook_deliveries()
        acked = {
            d.get("event_id")
            for d in deliveries
            if d.get("acked") is True and not d.get("duplicate") and not d.get("tampered")
        }
        events_ok = expect_events.issubset(acked)
        tamper_ok = True
        if expect_tampered:
            tamper_ok = any(
                d.get("tampered") and _rejected(d.get("status_code")) for d in deliveries
            )
        if events_ok and tamper_ok:
            time.sleep(1.0)
            return True
        time.sleep(_DRAIN_POLL_S)
    return False


def drain_checkpoint_events(
    ctx,
    steps: list[tuple[int, set[str]]],
    *,
    expect_tampered_at: set[int] | None = None,
    timeout_s: float = _DRAIN_TIMEOUT_S,
) -> tuple[list[tuple[int, bool]], list[dict[str, Any]]]:
    """Recreate the vendor through each ``(checkpoint, expected_event_ids)``
    step IN ORDER, draining after each individual recreate.

    The dispatcher only ever queues events for the single half-open window
    ``(checkpoint-1, checkpoint]`` on a given boot, never cumulative, and
    every boot also truncates ``webhook_deliveries.jsonl`` to empty. Collects
    and concatenates each step's delivery log (read right after that step's
    drain, before the next recreate wipes it) so callers can inspect the full
    cross-checkpoint history afterwards.

    Returns ``(per_step, all_deliveries)`` where ``per_step`` is
    ``[(checkpoint, ok), ...]`` in the same order as ``steps`` -- callers
    grade each checkpoint's own delivery independently (a connector that acks
    some of this run's events but not others should not hide behind a single
    aggregate AND across every step).
    """
    handle = ctx.vendor(VENDOR)
    all_deliveries: list[dict[str, Any]] = []
    per_step: list[tuple[int, bool]] = []
    for cp, expect_events in steps:
        handle.recreate(checkpoint=cp)
        want_tamper = expect_tampered_at is None or cp in expect_tampered_at
        ok = drain_webhooks(
            ctx, expect_events=expect_events, expect_tampered=want_tamper, timeout_s=timeout_s
        )
        per_step.append((cp, ok))
        all_deliveries.extend(handle.webhook_deliveries())
    return per_step, all_deliveries


def assert_never_delivered(ctx, event_id: str, settle_s: float = 12.0) -> bool:
    """Watch the delivery log for ``settle_s`` and return True iff
    ``event_id`` NEVER appears -- i.e. it genuinely never received a single
    delivery attempt (dropped before dispatch, not "attempted and failed")."""
    handle = ctx.vendor(VENDOR)
    deadline = time.monotonic() + settle_s
    while time.monotonic() < deadline:
        deliveries = handle.webhook_deliveries()
        if any(d.get("event_id") == event_id for d in deliveries):
            return False
        time.sleep(1.0)
    deliveries = handle.webhook_deliveries()
    return not any(d.get("event_id") == event_id for d in deliveries)


def ack_bookkeeping_well_formed_by_event(
    deliveries: list[dict[str, Any]]
) -> dict[str, tuple[bool, bool, str]]:
    """Per-event_id breakdown of the ack-required delivery log's own internal
    consistency, for every CANONICAL (non-duplicate, non-tampered) event_id
    seen (split from a single scenario-wide aggregate so a connector that acks
    SOME of this run's events but not others cannot hide behind one AND).

    The vendor computes `acked` itself, per attempt, as an exact comparison
    against `hex(hmac_sha256(secret, event_id + "." + that attempt's own
    X-TL-Timestamp))` (docs/webhooks.md) -- the log does not additionally
    record the raw header/token, so this check verifies the RECIPE'S
    signature -- fresh-per-attempt, never reused -- through the only
    evidence the vendor's own log exposes: attempt numbering and the
    resulting acked flag. A connector that echoed a stale/cached token would
    show acked:false on every attempt (since each attempt's expected token
    is derived from THAT attempt's own timestamp) and would never reach a
    terminal acked:true -- which is exactly what this checks for.

    Returns ``{event_id: (reached_acked, well_formed, detail)}``:
      - reached_acked: THIS event_id reached acked:true at least once.
      - well_formed: for this event_id, attempts are contiguous from 1, and
        an acked:true attempt (if any) is the LAST attempt ever logged for
        it -- no event is retried again after landing.
    An event_id with zero canonical attempts logged (no evidence at all) is
    simply absent from the returned dict -- callers must not synthesize a
    verdict for an event that was never even attempted here.
    """
    canonical = [d for d in deliveries if not d.get("duplicate") and not d.get("tampered")
                 and d.get("acked") is not None]
    by_event: dict[str, list[dict[str, Any]]] = {}
    for d in canonical:
        by_event.setdefault(d.get("event_id"), []).append(d)

    result: dict[str, tuple[bool, bool, str]] = {}
    for event_id, attempts in by_event.items():
        attempts.sort(key=lambda d: d.get("attempt", 0))
        numbers = [d.get("attempt") for d in attempts]
        if numbers != list(range(1, len(numbers) + 1)):
            result[event_id] = (False, False, f"{event_id}: non-contiguous attempts {numbers}")
            continue
        acked_positions = [i for i, d in enumerate(attempts) if d.get("acked") is True]
        if not acked_positions:
            result[event_id] = (False, True, f"{event_id}: never reached acked:true")
            continue
        if acked_positions != [len(attempts) - 1]:
            result[event_id] = (True, False, (
                f"{event_id}: acked attempt was not the final logged attempt "
                f"(acked_positions={acked_positions}, total={len(attempts)})"
            ))
            continue
        result[event_id] = (True, True, f"{event_id}: well-formed")
    return result


# ---------------------------------------------------------------------------
# Direct, verifier-owned calls to the vendor's dead-letter-queue endpoints.
# These are the ONLY way to prove `dlq_drained_empty` as a fact about the
# vendor's own authoritative state, independent of what the connector's own
# canonical store claims.
# ---------------------------------------------------------------------------

def _mint_token(ctx) -> str:
    base = ctx.vendor(VENDOR).base_url
    form = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": ctx.secrets["TL_CLIENT_ID"],
        "client_secret": ctx.secrets["TL_CLIENT_SECRET"],
    }).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/token", data=form, method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())["access_token"]


def _dlq_request(ctx, method: str, path: str) -> tuple[int, bytes]:
    base = ctx.vendor(VENDOR).base_url
    token = _mint_token(ctx)
    req = urllib.request.Request(
        f"{base}{path}", method=method,
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.getcode(), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def list_dead_letters(ctx) -> list[dict[str, Any]]:
    status, body = _dlq_request(ctx, "GET", "/v1/dead_letters")
    if status != 200:
        raise RuntimeError(f"GET /v1/dead_letters failed: {status} {body[:200]!r}")
    return json.loads(body)["items"]


def wait_for_dead_letter(ctx, event_id: str, timeout_s: float = 20.0) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        for item in list_dead_letters(ctx):
            if item.get("event_id") == event_id:
                return item
        time.sleep(1.0)
    return None


def row_diff(rows: list | None, want: list) -> list[dict]:
    """Per-source_id, per-field comparison against an answer key.

    Restores the signal of the deleted `backfill_candidates_match_fixture` and
    `backfill_applications_match_fixture` blob compares. Their removal left the
    cp0 backfill phase of BOTH scenarios graded for exit code and store
    readability only — no content at all — even though every later claim in this
    task (webhook freshness, no-regression, DLQ recovery) is stated relative to
    that baseline.

    Order-insensitive by source_id: dump order is not part of the contract.
    """
    if rows is None:
        return [{"source_id": "<no output>", "field": "<store unreadable>"}]
    got_by_id = {r.get("source_id"): r for r in rows}
    want_by_id = {r.get("source_id"): r for r in want}
    diffs: list[dict] = []
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


def grade_cp0_backfill(ctx, candidates: list, applications: list) -> None:
    """The two restored cp0 answer-key comparisons, shared by both scenarios so
    the repeated check names cannot drift apart in value.

    0/-1 by MEASUREMENT: the empty probe passes both (the sidecar records
    `backfill_candidates_match_fixture` / `backfill_applications_match_fixture`
    as empty=True — backfill is provided, complete plumbing). A correct baseline
    earns nothing here; only losing it costs.
    """
    for entity, rows, fixture_name in (
        ("candidates", candidates, "candidates_checkpoint_0.json"),
        ("applications", applications, "applications_checkpoint_0.json"),
    ):
        want = load_fixture(ctx, fixture_name)
        diffs = row_diff(rows, want)
        if diffs:
            shown = json.dumps(diffs[:3], sort_keys=True, default=str)
            more = f" (+{len(diffs) - 3} more)" if len(diffs) > 3 else ""
            detail = (f"{entity}@cp0: rows={len(rows or [])} expected={len(want)}; "
                      f"{len(diffs)} field diff(s): {shown}{more}")
        else:
            detail = f"{entity}@cp0: {len(rows or [])} row(s), every field matches"
        ctx.check(
            f"backfill_{entity}_rows_exact",
            not diffs,
            detail,
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )

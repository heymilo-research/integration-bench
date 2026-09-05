"""Shared helpers for the task-0049 (Placemint summit #2) scenarios.

Key mechanics:

  - ``placemint_summit serve`` is a long-lived HTTP listener the vendor must
    reach at ``http://connector:4000``. A ``docker compose run`` container
    does NOT get a service network alias, so we drive `serve` by bringing the
    app *service* up (``docker compose up -d app``) — which carries the
    `connector` alias generated from the task contract — and stopping it
    afterwards. One-shot subcommands (writeback / dump) still go through
    ``ctx.app.run([...])``.
  - Fault envs (``FAULT_5XX_ON_PAGE``, ``FAULT_TOKEN_EXPIRY_MIDRUN``,
    ``FAULT_DUP_STORM``, ``FAULT_DROP_EVENT_IDS``) are set the same way
    ``VendorHandle.recreate()`` sets ``CHECKPOINT`` internally — via the
    stack's ``vendor_envs`` override, applied on the vendor service's next
    recreate — since ``recreate()`` itself only exposes the checkpoint knob.
"""

from __future__ import annotations

import json
import time
from typing import Any

from bench.verifier.io import read_json_output

VENDOR = "placemint"
APP_SERVICE = "app"
_DRAIN_TIMEOUT_S = 15.0
_DRAIN_POLL_S = 0.25


def _stack(ctx):
    return ctx.app._stack


def recreate_with_faults(
    ctx, *, checkpoint: int, faults: dict[str, str] | None = None
) -> None:
    """Recreate the vendor at ``checkpoint`` with the given fault env vars
    layered on top (cleared first, so a scenario never inherits a fault left
    set by an earlier one).

    Must key ``stack.vendor_envs`` by the RESOLVED COMPOSE SERVICE NAME
    (``handle._service``, e.g. ``"vendor"``), not the task.yaml vendors-block
    name (``VENDOR`` == ``"placemint"``) — the generated Compose configuration
    only emits real service names, so writing
    fault envs under the vendor-block name silently orphans them (they never
    reach the container's actual environment).
    """
    handle = ctx.vendor(VENDOR)
    stack = _stack(ctx)
    base = {
        "FAULT_5XX_ON_PAGE": "",
        "FAULT_TOKEN_EXPIRY_MIDRUN": "0",
        "FAULT_DUP_STORM": "0",
        "FAULT_DROP_EVENT_IDS": "",
    }
    base.update(faults or {})
    stack.vendor_envs.setdefault(handle._service, {}).update(base)
    handle.recreate(checkpoint=checkpoint)


def _app_running(stack) -> bool:
    """True while the app service still has a live container.

    ``docker compose ps`` without ``-a`` lists running containers only, so a
    service absent from the listing is already down.

    Deliberately does NOT swallow: an earlier version returned False on any
    exception, which turned a missing `ComposeUnitStack.ps` into a silent "it's
    already down" and made the whole stop-then-wipe sequence a no-op. Being
    unable to observe the app is not evidence that it stopped.
    """
    rows = stack.ps()
    for row in rows:
        if (row.get("Service") or row.get("service")) != APP_SERVICE:
            continue
        state = str(row.get("State") or row.get("state") or "").lower()
        return state.startswith("running") or state.startswith("restarting")
    return False


def _wait_app_stopped(stack, timeout_s: float = 60.0) -> None:
    """Wait until Compose confirms the long-lived connector is down."""
    deadline = time.monotonic() + timeout_s
    while _app_running(stack):
        if time.monotonic() >= deadline:
            raise RuntimeError(
                f"app service still running {timeout_s:.0f}s after `compose stop`"
            )
        time.sleep(0.25)


def reset_store(ctx) -> None:
    """Wipe the connector's durable JSON store so each scenario starts fresh.

    The store lives at STATE_PATH, which the harness points under the /data
    bind (bench/compose_unit.py) -- the only app mount that survives across
    separate `docker compose run` invocations. Wiping it from a throwaway `run`
    therefore reaches the same file `serve` will write.

    It used to `rm /app/state/store.json`, the connector's compile-time default,
    which lived only in each container's own layer: `serve` wrote 201 placements
    into the service container and every `run app dump` read an empty store, so
    every store check in this task measured rows=0 for gold (2026-08-07).
    """
    stack = _stack(ctx)
    # Unlink on the HOST, not via `docker compose run`. /data is a bind mount to
    # eval_dir.canonical_data_dir and the verifier runs on the host, so this is
    # the one form that cannot be defeated by container user/permissions or by
    # shell expansion of $STATE_PATH inside a throwaway container.
    host_state = stack.eval_dir.canonical_data_dir / "app_store.json"

    # The unlink is only final once the previous scenario's `serve` is GONE.
    # _PollThread calls Store.save() every poll cycle, so a live serve rewrites
    # the file from memory within a few seconds of any delete, and `serve_start`
    # then force-recreates a fresh process that LOADS that rewritten file.
    # Because Store.apply_record upserts, the next scenario's cp0 backfill can
    # never evict the resurrected rows.
    #
    # Measured 2026-08-07 against a live app container: `rm app_store.json`
    # followed by an 8s wait returned the file with all 201 rows intact,
    # including plc_90001 -- a row TIMELINE only creates at CHECKPOINT>=3. That
    # is exactly how scenario 1 (which walks to cp3) poisoned the two scenarios
    # that assert cp2 state, failing reconcile_rows_exact and
    # cp2_intermediate_rows_exact with `<unexpected row> plc_90001` while every
    # other field matched. Stop-then-unlink was not enough on its own: the
    # stop's error was swallowed, so a serve that outlived it did so silently.
    try:
        stack.stop_service(APP_SERVICE)
    except Exception:
        # A benign compose hiccup here must not break scenario 1 (where there is
        # no app container to stop). The wait loop below is the real gate.
        pass
    _wait_app_stopped(stack)

    try:
        host_state.unlink()
    except FileNotFoundError:
        pass

    # Prove the wipe held. With the app service confirmed down nothing can
    # legitimately recreate this file, so a survivor means the reset raced a
    # writer we do not know about -- fail loudly rather than silently grading
    # the next scenario against poisoned state.
    if host_state.exists():
        raise RuntimeError(
            f"{host_state} reappeared after unlink with the app service down"
        )


def dump_placements(ctx) -> list[dict[str, Any]] | None:
    out = ctx.output_dir / "placements.json"
    try:
        out.unlink()
    except OSError:
        pass
    code, _stdout, _stderr = ctx.app.run(["dump"])
    if code != 0:
        return None
    return read_json_output(out, timeout_s=15.0)


def load_fixture(ctx, name: str) -> Any:
    return json.loads((ctx.fixtures / name).read_text(encoding="utf-8"))


def read_output(ctx, name: str, *, exit_code: int) -> Any:
    return read_json_output(
        ctx.output_dir / name, timeout_s=15.0 if exit_code == 0 else 0.5
    )


def clear_outputs(ctx) -> None:
    for name in ("placements.json", "writeback_result.json"):
        try:
            (ctx.output_dir / name).unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Serve lifecycle (service up/stop so the vendor can reach http://connector:4000)
# ---------------------------------------------------------------------------


def serve_start(ctx) -> None:
    stack = _stack(ctx)
    stack.up(service=APP_SERVICE, force_recreate=True)
    _wait_listener(stack)


def serve_stop(ctx) -> None:
    stack = _stack(ctx)
    try:
        stack.stop_service(APP_SERVICE)
    except Exception:
        # `compose stop` may report a benign error when no app container exists.
        # It is only safe to continue if Compose also proves nothing is running.
        if _app_running(stack):
            raise
    _wait_app_stopped(stack)


def _wait_listener(stack, timeout_s: float = 60.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        probe = stack.exec(
            "vendor",
            "python",
            "-c",
            "import urllib.request,sys;"
            "sys.exit(0 if urllib.request.urlopen('http://connector:4000/',timeout=2).status==200 else 1)",
            check=False,
        )
        if probe.returncode == 0:
            return
        time.sleep(0.5)


# ---------------------------------------------------------------------------
# Webhook / log inspection
# ---------------------------------------------------------------------------


def is_2xx(code: Any) -> bool:
    try:
        return 200 <= int(code) < 300
    except (TypeError, ValueError):
        return False


def wait_for_webhook_ids(
    ctx, event_ids: set[str], *, timeout_s: float = _DRAIN_TIMEOUT_S
) -> bool:
    """Poll the vendor delivery log until every id in ``event_ids`` has at
    least one attempt logged (delivered, whether or not dropped/deduped
    downstream — this only proves the VENDOR tried; store convergence is
    checked separately via ``dump_placements``)."""
    handle = ctx.vendor(VENDOR)
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        deliveries = handle.webhook_deliveries()
        seen = {d.get("event_id") for d in deliveries}
        if event_ids.issubset(seen):
            time.sleep(0.3)
            return True
        time.sleep(_DRAIN_POLL_S)
    return False


def offset_of(entry: dict[str, Any]) -> int:
    try:
        return int((entry.get("query") or {}).get("offset") or 0)
    except (TypeError, ValueError):
        return -1


def row_diff(
    rows: list[dict[str, Any]] | None, want: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Per-source_id, per-field comparison against an answer key.

    Replaces the ten `store == fixture` / `output == fixture` compares in this
    task: `cp0_backfill_matches_fixture`, `webhook_freshness_matches_fixture`,
    `reconcile_matches_fixture`, `summit_matches_fixture`,
    `writeback_matches_fixture`, and the per-checkpoint
    `cp2_intermediate_matches_fixture`, `cp3_intermediate_matches_fixture`,
    `cp4_intermediate_matches_fixture`, `cp5_intermediate_matches_fixture`,
    `cp6_intermediate_matches_fixture`.

    This is the task where the difference matters most: it walks seven
    checkpoints under four compounding faults, and every failure mode it can
    produce — a dropped webhook never reconciled, a re-applied stale event, a
    writeback retry that minted a duplicate — lands as a handful of wrong FIELDS
    at an unchanged row count. `store rows=N fixture rows=N` was the only detail
    the old compares could give, which is no detail at all across a
    seven-checkpoint walk.
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
                diffs.append(
                    {
                        "source_id": sid,
                        "field": key,
                        "want": w.get(key),
                        "got": g.get(key),
                    }
                )
    return diffs


def ref_diff(
    items: list[dict[str, Any]] | None, want: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Per-client_ref, per-field comparison of writeback_result.json's writes."""
    if items is None:
        return [{"client_ref": "<no output>", "field": "<missing or unreadable>"}]
    got_by = {i.get("client_ref"): i for i in items}
    want_by = {i.get("client_ref"): i for i in want}
    diffs: list[dict[str, Any]] = []
    for k in sorted(set(want_by) | set(got_by), key=str):
        w, g = want_by.get(k), got_by.get(k)
        if g is None:
            diffs.append({"client_ref": k, "field": "<missing item>"})
            continue
        if w is None:
            diffs.append({"client_ref": k, "field": "<unexpected item>"})
            continue
        for field in sorted(set(w) | set(g)):
            if w.get(field) != g.get(field):
                diffs.append(
                    {
                        "client_ref": k,
                        "field": field,
                        "want": w.get(field),
                        "got": g.get(field),
                    }
                )
    return diffs


def diff_detail(
    label: str, rows: list | None, want: list, diffs: list, limit: int = 3
) -> str:
    n = "none" if rows is None else len(rows)
    if not diffs:
        return f"{label}: {n} row(s), every field matches the answer key"
    shown = json.dumps(diffs[:limit], sort_keys=True, default=str)
    more = f" (+{len(diffs) - limit} more)" if len(diffs) > limit else ""
    return f"{label}: rows={n} expected={len(want)}; {len(diffs)} field diff(s): {shown}{more}"


def _persisted_store(ctx) -> dict[str, Any] | None:
    """Read the connector's durable store straight off the host.

    STATE_PATH lives under the /data bind (bench/compose_unit.py), which the
    verifier can read directly — no `docker compose run` round-trip, so this is
    cheap enough to poll in a loop.
    """
    path = _stack(ctx).eval_dir.canonical_data_dir / "app_store.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def wait_for_persisted(
    ctx, predicate, *, timeout_s: float = 45.0, poll_s: float = 0.5
) -> bool:
    """Block until the PERSISTED store satisfies ``predicate(store_dict)``.

    Why this exists: `wait_for_webhook_ids` waits for the vendor to record a
    delivery as ACKED, which is not the same as the connector having applied and
    saved it. The connector acks first and applies asynchronously, so a dump
    taken right after the ack races the apply. Measured on 2026-08-07: scenario 1
    asserted its cp1/cp2/cp3 state immediately after the acks and read 200 base
    rows with plc_00042 still at its seed value `fell_through`, while the same
    store at end-of-run was fully correct (201 placements, all 7 event ids seen).
    Waiting for DELIVERY and asserting on EFFECT is the defect; this waits for
    the effect.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        store = _persisted_store(ctx)
        if store is not None:
            try:
                if predicate(store):
                    return True
            except (KeyError, TypeError, AttributeError):
                pass
        time.sleep(poll_s)
    return False


def events_seen(*event_ids: str):
    """Predicate: the connector has persisted these event ids as processed.

    Entity-agnostic (works for placement/client/note events alike), which is why
    it is preferred over asserting a specific field value.
    """
    wanted = set(event_ids)

    def _pred(store: dict[str, Any]) -> bool:
        return wanted <= set(store.get("seen_event_ids") or [])

    return _pred

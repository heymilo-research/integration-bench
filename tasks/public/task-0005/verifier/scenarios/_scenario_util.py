"""Shared helpers for the task-0005 scenarios.

These wrap the harness's ComposeStack/AppHandle/VendorHandle so each scenario
reads as a short sequence of intent-level steps. Nothing here mutates the
harness — it only uses the stack objects the harness hands the scenario.

Key mechanic: ``connector serve`` is a long-lived HTTP listener that the vendor
must reach at ``http://app:4000``. A ``docker compose run`` container does NOT get
the ``app`` service network alias, so we drive serve by bringing the *service*
up (``docker compose up -d app``), which DOES get the alias, and stopping it
afterwards. One-shot subcommands (sync / dump) still go through
``ctx.app.run([...])``.
"""

from __future__ import annotations

import json
import time
from typing import Any

from bench.verifier.io import read_json_output

APP_SERVICE = "app"
_DRAIN_TIMEOUT_S = 15.0
_DRAIN_POLL_S = 0.25


def _stack(ctx):
    # AppHandle stores the ComposeStack the harness created for this grade.
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
# Store inspection
# ---------------------------------------------------------------------------

def dump_store(ctx) -> list[dict[str, Any]] | None:
    """Snapshot the canonical store to output/candidates.json and read it back."""
    out = ctx.output_dir / "candidates.json"
    # Remove any stale snapshot so we never read a previous scenario's file.
    try:
        out.unlink()
    except OSError:
        pass
    code, _stdout, _stderr = ctx.app.run(["dump"])
    if code != 0:
        return None
    return read_json_output(out, timeout_s=15.0)


def load_fixture(ctx, name: str) -> list[dict[str, Any]]:
    return json.loads((ctx.fixtures / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Serve lifecycle (service up/stop so the vendor can reach http://app:4000)
# ---------------------------------------------------------------------------

def serve_start(ctx) -> None:
    """Bring the app serve listener up as a real service (gets the `app` alias)."""
    stack = _stack(ctx)
    stack.up(service=APP_SERVICE, force_recreate=True)
    _wait_listener(stack)


def serve_stop(ctx) -> None:
    stack = _stack(ctx)
    try:
        stack.stop_service(APP_SERVICE)
    except Exception:
        pass


def _wait_listener(stack, timeout_s: float = 30.0) -> None:
    """Wait until the serve container answers on its port from inside the vendor."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        probe = stack.exec(
            "vendor",
            "python",
            "-c",
            "import urllib.request,sys;"
            "sys.exit(0 if urllib.request.urlopen('http://app:4000/',timeout=2).status==200 else 1)",
            check=False,
        )
        if probe.returncode == 0:
            return
        time.sleep(0.5)


# ---------------------------------------------------------------------------
# Webhook draining
# ---------------------------------------------------------------------------

def drain_webhooks(
    ctx,
    *,
    expect_events: set[str],
    expect_tampered: bool = False,
    timeout_s: float = _DRAIN_TIMEOUT_S,
) -> bool:
    """Poll the vendor delivery log until every id in ``expect_events`` has a 2xx
    ack (and, if ``expect_tampered``, at least one tampered delivery has been
    rejected non-2xx by the live listener). Returns True on success."""
    handle = ctx.vendor("talentforge")
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
            # Small settle so any in-flight duplicate is also logged.
            time.sleep(0.3)
            return True
        time.sleep(_DRAIN_POLL_S)
    return False


def _is_2xx(code: Any) -> bool:
    try:
        return 200 <= int(code) < 300
    except (TypeError, ValueError):
        return False


def _rejected(code: Any) -> bool:
    """A live listener that rejected a delivery returns a non-2xx status."""
    if code is None:
        return False
    try:
        return not (200 <= int(code) < 300)
    except (TypeError, ValueError):
        return False

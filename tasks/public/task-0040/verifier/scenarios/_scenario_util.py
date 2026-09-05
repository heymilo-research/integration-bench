"""Shared helpers for the task-0040 (Interviewly event-confirmed writeback)
scenarios.

Same `serve` mechanics as task-0038/task-0039: `interviewly_writeback serve`
must be reachable at the `connector` network alias for the vendor's
background confirmation-event delivery to land, so bring it up as a real
compose *service* (`docker compose up -d app`), not a one-shot `run`. `push`
and `dump` remain one-shot ``ctx.app.run([...])`` calls — only the vendor's
OUTBOUND confirmation POST needs the alias, not the connector's own
short-lived push/dump invocations.

Important vendor quirk (docs/writeback.md + main.py): the `202` reschedule
ack never mutates the vendor's canonical `GET /v1/interviews/{id}` state —
only the subsequent `interview.rescheduled` EVENT carries the new
`scheduled_at`. So `reconcile_pending`'s poll backstop cannot converge a
connector-originated reschedule that never got a confirming event; it exists
for the documented "delivery can rarely be lost" case in general, not as a
substitute path exercised by these scenarios. These scenarios exercise the
primary event-confirmed path.
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
    return ctx.app._stack


def read_output(ctx, filename: str) -> Any:
    return read_json_output(ctx.output_dir / filename, timeout_s=10.0)


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
            "vendor",
            "python3",
            "-c",
            "import urllib.request,sys;"
            "sys.exit(0 if urllib.request.urlopen('http://connector:4000/',timeout=2).status==200 else 1)",
            check=False,
        )
        if probe.returncode == 0:
            return
        time.sleep(0.5)


def wait_for_status(ctx, client_ref: str, expected: str, *, timeout_s: float = 60.0) -> dict[str, Any] | None:
    """Poll `dump`'s writeback_result.json until `client_ref` reaches
    `expected` status (or timeout). Returns the matching record, or None."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        ctx.app.run(["dump"])
        result = read_output(ctx, "writeback_result.json") or {}
        for row in result.get("reschedules", []):
            if row.get("client_ref") == client_ref:
                if row.get("status") == expected:
                    return row
                break
        time.sleep(_DRAIN_POLL_S)
    return None

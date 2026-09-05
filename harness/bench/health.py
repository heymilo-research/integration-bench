"""Small readiness-wait helper: block until a compose service's published
port answers HTTP, bounded by a timeout. Used after `docker compose up -d`
before handing control to the agent (rollout) or the verifier (grade)."""

from __future__ import annotations

import time

import httpx


class HealthCheckError(RuntimeError):
    pass


def wait_for_http(url: str, *, timeout_s: float = 60.0, interval_s: float = 1.0) -> None:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            httpx.get(url, timeout=5.0)
            return
        except httpx.HTTPError as exc:  # connection refused, etc.
            last_error = exc
            time.sleep(interval_s)
    raise HealthCheckError(f"{url} did not become reachable within {timeout_s}s: {last_error}")

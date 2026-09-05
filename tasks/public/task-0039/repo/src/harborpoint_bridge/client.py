"""Rosterly HTTP transport.

    POST /oauth/token                        -> {"access_token": ..., "expires_in": ...}
    GET  /api/shifts?offset=&limit=           -> {"data": [...], "total": n, ...}
    GET  /api/shifts/{shift_id}               -> one shift, or 404
    POST /api/workers/{worker_id}/notes       -> 201, the created note

The token goes in the ``Authorization`` header on every ``/api`` request; never
in the query string. ``GET /`` is the only unauthenticated route.

The vendor documentation is in ``docs/``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from harborpoint_bridge.config import Config

_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 6
_BACKOFF_S = 0.4
_MAX_BACKOFF_S = 10.0
_TOKEN_MARGIN_S = 60.0


class RosterlyError(RuntimeError):
    pass


class RosterlyNotFound(RosterlyError):
    pass


class RosterlyClient:
    """Thin transport. Counts what it did so the caller can report on it."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.pages_fetched = 0
        self.notes_written = 0
        self._token: str | None = None
        self._token_expires_at = 0.0

    # -- auth ---------------------------------------------------------------
    def _access_token(self) -> str:
        if self._token is not None and time.monotonic() < self._token_expires_at:
            return self._token
        body = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self.cfg.client_id,
            "client_secret": self.cfg.client_secret,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.cfg.vendor_base_url}/oauth/token",
            data=body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as exc:
            raise RosterlyError(f"token request -> {exc.code}") from exc
        self._token = str(payload["access_token"])
        ttl = float(payload.get("expires_in") or 3600)
        self._token_expires_at = time.monotonic() + max(ttl - _TOKEN_MARGIN_S, 1.0)
        return self._token

    # -- requests -----------------------------------------------------------
    def _request(self, method: str, path: str, *,
                 params: dict[str, Any] | None = None,
                 body: dict[str, Any] | None = None,
                 idempotency_key: str | None = None) -> Any:
        url = f"{self.cfg.vendor_base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        data = json.dumps(body).encode("utf-8") if body is not None else None

        last: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            headers = {"Authorization": f"Bearer {self._access_token()}"}
            if data is not None:
                headers["Content-Type"] = "application/json"
            if idempotency_key:
                headers["Idempotency-Key"] = idempotency_key
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code == 404:
                    raise RosterlyNotFound(f"{method} {path} -> 404") from exc
                if exc.code == 401:
                    self._token = None
                    continue
                if exc.code not in _RETRY_STATUS:
                    detail: Any = {}
                    try:
                        detail = json.load(exc)
                    except Exception:  # noqa: BLE001
                        pass
                    raise RosterlyError(f"{method} {path} -> {exc.code}: {detail}") from exc
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                delay = (
                    min(float(retry_after), _MAX_BACKOFF_S)
                    if retry_after
                    else min(_BACKOFF_S * (2 ** attempt), _MAX_BACKOFF_S)
                )
                time.sleep(delay)
            except urllib.error.URLError as exc:
                last = exc
                time.sleep(_BACKOFF_S)
        raise RosterlyError(f"{method} {path} gave up after {_MAX_ATTEMPTS} attempts: {last}")

    # -- reads --------------------------------------------------------------
    def shift_page(self, *, offset: int, limit: int | None = None) -> dict[str, Any]:
        """One page of the shift collection, envelope verbatim."""
        envelope = self._request(
            "GET", "/api/shifts",
            params={"offset": offset, "limit": limit or self.cfg.page_limit},
        )
        self.pages_fetched += 1
        return envelope

    def get_shift(self, shift_id: str) -> dict[str, Any]:
        """One shift by id. Raises ``RosterlyNotFound`` when Rosterly has none."""
        return self._request("GET", f"/api/shifts/{shift_id}")

    # -- writes -------------------------------------------------------------
    def create_note(self, worker_id: str, body: str, *, author: str,
                    idempotency_key: str) -> dict[str, Any]:
        """Attach one note to a worker. Returns the note Rosterly stored."""
        record = self._request(
            "POST", f"/api/workers/{worker_id}/notes",
            body={"body": body, "author": author},
            idempotency_key=idempotency_key,
        )
        self.notes_written += 1
        return record

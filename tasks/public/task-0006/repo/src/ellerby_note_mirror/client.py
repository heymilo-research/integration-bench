"""Rosterly HTTP transport.

    POST /oauth/token                                  -> access token
    GET  /api/workers?offset=&limit=                   -> one page of the roster
    GET  /api/workers/{worker_id}/notes?offset=&limit= -> one page of a carer's
                                                          case notes

The token goes in the ``Authorization`` header on every ``/api`` request; never
in the query string. ``GET /`` is the only unauthenticated route.

``page()`` hands the caller the raw outcome -- status, envelope and whatever
``Retry-After`` came back with it -- and decides nothing about what a non-200
means. Transport retries a 5xx with backoff, because that is what
``docs/pagination.md`` asks for; everything else is the caller's call.

The vendor documentation is in ``docs/``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ellerby_note_mirror.config import Config

_SERVER_ERROR_STATUS = {500, 502, 503, 504}
_MAX_ATTEMPTS = 5
_BACKOFF_S = 0.4
_MAX_BACKOFF_S = 10.0
_TOKEN_MARGIN_S = 60.0


class RosterlyError(RuntimeError):
    pass


class Outcome:
    """One HTTP outcome: the status, the decoded body, and the advice with it."""

    def __init__(self, status: int, body: Any, retry_after: float | None) -> None:
        self.status = status
        self.body = body
        self.retry_after = retry_after

    @property
    def ok(self) -> bool:
        return self.status == 200


class RosterlyClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.requests_made = 0
        self._token: str | None = None
        self._token_expires_at = 0.0

    def _access_token(self) -> str:
        if self._token is not None and time.monotonic() < self._token_expires_at:
            return self._token
        body = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self.cfg.client_id,
            "client_secret": self.cfg.client_secret,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.cfg.vendor_base_url}/oauth/token", data=body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as exc:
            raise RosterlyError(f"token request -> {exc.code}") from exc
        self._token = str(payload["access_token"])
        ttl = float(payload.get("expires_in") or 3600)
        self._token_expires_at = time.monotonic() + max(ttl - _TOKEN_MARGIN_S, 1.0)
        return self._token

    def page(self, path: str, params: dict[str, Any]) -> Outcome:
        """One GET. 5xx is retried; anything else is handed straight back."""
        url = f"{self.cfg.vendor_base_url}{path}?{urllib.parse.urlencode(params)}"
        for attempt in range(_MAX_ATTEMPTS):
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {self._access_token()}"})
            self.requests_made += 1
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return Outcome(resp.status, json.load(resp), None)
            except urllib.error.HTTPError as exc:
                if exc.code == 401:
                    self._token = None
                    continue
                if exc.code in _SERVER_ERROR_STATUS:
                    time.sleep(min(_BACKOFF_S * (2 ** attempt), _MAX_BACKOFF_S))
                    continue
                advice = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    retry_after = float(advice) if advice is not None else None
                except (TypeError, ValueError):
                    retry_after = None
                return Outcome(exc.code, None, retry_after)
            except urllib.error.URLError:
                time.sleep(_BACKOFF_S)
        raise RosterlyError(f"GET {path} gave up after {_MAX_ATTEMPTS} attempts")

    def roster_page(self, offset: int) -> Outcome:
        return self.page("/api/workers",
                         {"offset": offset, "limit": self.cfg.page_limit})

    def notes_page(self, worker_id: str, offset: int) -> Outcome:
        return self.page(f"/api/workers/{worker_id}/notes",
                         {"offset": offset, "limit": self.cfg.page_limit})

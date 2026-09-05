"""Rosterly HTTP transport.

    POST /oauth/token                          -> access token
    GET  /api/{collection}?offset=&limit=      -> one page of a collection

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

from nordhavn_mirror_port.config import Config

_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 6
_BACKOFF_S = 0.4
_MAX_BACKOFF_S = 10.0
_TOKEN_MARGIN_S = 60.0


class RosterlyError(RuntimeError):
    pass


class RosterlyClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.pages_fetched = 0
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

    def _get(self, path: str, params: dict[str, Any]) -> Any:
        url = f"{self.cfg.vendor_base_url}{path}?{urllib.parse.urlencode(params)}"
        last: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {self._access_token()}"})
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code == 401:
                    self._token = None
                    continue
                if exc.code not in _RETRY_STATUS:
                    raise RosterlyError(f"GET {path} -> {exc.code}") from exc
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                delay = (
                    min(float(retry_after), _MAX_BACKOFF_S) if retry_after
                    else min(_BACKOFF_S * (2 ** attempt), _MAX_BACKOFF_S))
                time.sleep(delay)
            except urllib.error.URLError as exc:
                last = exc
                time.sleep(_BACKOFF_S)
        raise RosterlyError(f"GET {path} gave up after {_MAX_ATTEMPTS} attempts: {last}")

    def collection(self, plural: str) -> list[dict[str, Any]]:
        """Every record in ``plural``, paged out."""
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            envelope = self._get(
                f"/api/{plural}", {"offset": offset, "limit": self.cfg.page_limit})
            self.pages_fetched += 1
            page = envelope.get("data") or []
            rows.extend(page)
            used = int(envelope.get("limit") or 0) or max(len(page), 1)
            offset += used
            if not page or offset >= int(envelope.get("total") or 0):
                return rows

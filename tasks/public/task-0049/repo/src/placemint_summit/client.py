"""Placemint HTTP client — auth + single-attempt requests (provided). See ``PROBLEM.md``."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

_TIMEOUT_S = 30.0
# Refresh this many seconds before the advertised expiry lapses — cheap
# insurance against request latency eating into the token's last second.
_REFRESH_SKEW_S = 5.0


def _request(
    method: str,
    url: str,
    headers: dict | None = None,
    body: bytes | None = None,
) -> tuple[int, bytes, dict]:
    """Low-level HTTP call (stdlib only). Returns (status, raw_body, headers).

    Never raises on a non-2xx response — HTTPError is caught and its status/
    body/headers are returned exactly like a normal response.
    """
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return resp.getcode(), resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def _parse_json(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


class PlacemintClient:
    """OAuth CC client with proactive refresh + single-attempt data-plane calls."""

    def __init__(self, base_url: str, client_id: str, client_secret: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: str | None = None
        self._expires_at = 0.0

    def get_token(self) -> dict[str, Any]:
        """Mint a fresh access token. Single attempt — no retry."""
        body = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }).encode("utf-8")
        status, raw, headers = _request(
            "POST",
            f"{self.base_url}/oauth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=body,
        )
        parsed = _parse_json(raw)
        parsed["_status"] = status
        parsed["_headers"] = headers
        return parsed

    def force_reauth(self) -> None:
        """Discard the cached token so the next call mints a fresh one."""
        self._token = None
        self._expires_at = 0.0

    def ensure_token(self) -> str | None:
        """Return a cached token, refreshing proactively before expiry.

        Returns ``None`` if minting failed (e.g. a 429/5xx on the token
        endpoint) so the caller can decide how to wait/retry — this method
        itself does not loop.
        """
        now = time.monotonic()
        if self._token is not None and now < self._expires_at:
            return self._token
        result = self.get_token()
        if result.get("_status") != 200 or "access_token" not in result:
            return None
        self._token = result["access_token"]
        self._expires_at = time.monotonic() + max(0.0, float(result["expires_in"]) - _REFRESH_SKEW_S)
        return self._token

    def _headers(self, *, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def raw_get(self, path: str) -> tuple[int, dict[str, Any], dict[str, str]]:
        """One authenticated GET attempt. No retry."""
        status, raw, headers = _request("GET", f"{self.base_url}{path}", headers=self._headers())
        return status, _parse_json(raw), headers

    def raw_post(
        self, path: str, body: dict[str, Any], *, idempotency_key: str | None = None
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        """One authenticated POST attempt. No retry."""
        headers = self._headers(idempotency_key=idempotency_key)
        headers["Content-Type"] = "application/json"
        payload = json.dumps(body).encode("utf-8")
        status, raw, resp_headers = _request(
            "POST", f"{self.base_url}{path}", headers=headers, body=payload
        )
        return status, _parse_json(raw), resp_headers

    def raw_patch(
        self, path: str, body: dict[str, Any], *, idempotency_key: str | None = None
    ) -> tuple[int, dict[str, Any], dict[str, str]]:
        """One authenticated PATCH attempt. No retry."""
        headers = self._headers(idempotency_key=idempotency_key)
        headers["Content-Type"] = "application/json"
        payload = json.dumps(body).encode("utf-8")
        status, raw, resp_headers = _request(
            "PATCH", f"{self.base_url}{path}", headers=headers, body=payload
        )
        return status, _parse_json(raw), resp_headers

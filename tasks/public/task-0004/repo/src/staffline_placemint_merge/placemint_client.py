"""Placemint HTTP client. See docs/placemint/."""

from __future__ import annotations

import time
from typing import Any

import requests


class PlacemintClient:
    def __init__(self, base_url: str, client_id: str, client_secret: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = timeout
        self._session = requests.Session()
        self._token: str | None = None
        self._token_expires_at: float = 0.0

    def _mint_token(self) -> None:
        resp = self._session.post(
            f"{self.base_url}/oauth/token",
            json={
                "grant_type": "client_credentials",
                "client_id": self._client_id,
                "client_secret": self._client_secret,
            },
            timeout=self._timeout,
        )
        resp.raise_for_status()
        body = resp.json()
        self._token = body["access_token"]
        # Refresh a little early so no request straddles expiry.
        self._token_expires_at = time.monotonic() + max(1, int(body.get("expires_in", 60)) - 5)

    def _ensure_token(self) -> None:
        if self._token is None or time.monotonic() >= self._token_expires_at:
            self._mint_token()

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> requests.Response:
        resp: requests.Response | None = None
        for _attempt in range(8):
            self._ensure_token()
            headers = {"Authorization": f"Bearer {self._token}"}
            if idempotency_key:
                headers["Idempotency-Key"] = idempotency_key
            url = f"{self.base_url}{path}"
            resp = self._session.request(
                method, url, params=params, json=json_body, headers=headers, timeout=self._timeout
            )
            if resp.status_code == 401:
                # Token died mid-run -- mint a fresh one and retry once.
                self._token = None
                continue
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "3") or "3")
                time.sleep(retry_after)
                continue
            return resp
        assert resp is not None
        return resp

    def get(self, path: str, params: dict[str, Any] | None = None) -> requests.Response:
        return self._request("GET", path, params=params)

    def list_all(self, path: str, page_size: int = 100) -> list[dict[str, Any]]:
        """Fetch all pages of a Placemint list endpoint."""
        out: list[dict[str, Any]] = []
        offset = 0
        while True:
            resp = self.get(path, params={"offset": offset, "limit": page_size})
            resp.raise_for_status()
            page = resp.json()
            data = page.get("data", [])
            out.extend(data)
            total = page.get("total", len(out))
            offset += page_size
            if offset >= total or not data:
                break
        return out

"""StaffLine HTTP client. See docs/staffline/."""

from __future__ import annotations

import hashlib
import hmac
import json as _json
import time
from typing import Any

import requests


class StafflineClient:
    def __init__(self, base_url: str, app_token: str, hmac_secret: str, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._app_token = app_token
        self._hmac_secret = hmac_secret
        self._timeout = timeout
        self._session = requests.Session()

    def _sign(self, body: bytes) -> tuple[str, str]:
        ts = str(int(time.time()))
        msg = ts.encode("utf-8") + b"." + body
        sig = hmac.new(self._hmac_secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
        return ts, sig

    def _headers(self, body: bytes) -> dict[str, str]:
        ts, sig = self._sign(body)
        return {
            "X-SL-Token": self._app_token,
            "X-SL-Timestamp": ts,
            "X-SL-Signature": sig,
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> requests.Response:
        body = b"" if json_body is None else _json.dumps(json_body).encode("utf-8")
        url = f"{self.base_url}{path}"
        resp: requests.Response | None = None
        for _attempt in range(8):
            headers = self._headers(body)
            resp = self._session.request(
                method, url, params=params, data=body or None, headers=headers, timeout=self._timeout
            )
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", "10") or "10")
                time.sleep(retry_after)
                continue
            return resp
        assert resp is not None
        return resp

    def get(self, path: str, params: dict[str, Any] | None = None) -> requests.Response:
        return self._request("GET", path, params=params)

    def post(self, path: str, params: dict[str, Any] | None, json_body: dict[str, Any]) -> requests.Response:
        return self._request("POST", path, params=params, json_body=json_body)

    def list_all(self, path: str, params: dict[str, Any] | None = None, page_size: int = 50) -> list[dict[str, Any]]:
        """Fetch all pages of a StaffLine list endpoint."""
        out: list[dict[str, Any]] = []
        start = 0
        base_params = dict(params or {})
        while True:
            page_params = dict(base_params)
            page_params["start"] = start
            page_params["count"] = page_size
            resp = self.get(path, params=page_params)
            resp.raise_for_status()
            page = resp.json()
            rows = page.get("rows", [])
            out.extend(rows)
            if not page.get("more"):
                break
            start += page_size
        return out

    def get_by_id(self, path: str) -> dict[str, Any] | None:
        """GET a single record by id; returns None if not found."""
        resp = self.get(path)
        resp.raise_for_status()
        body = resp.json()
        if not body:
            return None
        return body

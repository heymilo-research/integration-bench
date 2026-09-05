"""Signed HTTP client for the StaffLine web-services API (provided). See ``PROBLEM.md``."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .config import Config


class VendorError(RuntimeError):
    """Raised for an unexpected (non-2xx) HTTP response from StaffLine."""

    def __init__(self, status: int, path: str, body: Any) -> None:
        super().__init__(f"StaffLine {status} on {path}: {body!r}")
        self.status = status
        self.path = path
        self.body = body


class StafflineClient:
    """Thin signed-request wrapper over StaffLine's ``/svc`` endpoints."""

    def __init__(self, config: Config) -> None:
        self._base_url = config.vendor_base_url
        self._token = config.app_token
        self._secret = config.hmac_secret

    # ------------------------------------------------------------------ auth
    def _sign(self, timestamp: str, body: bytes) -> str:
        msg = timestamp.encode("utf-8") + b"." + body
        return hmac.new(self._secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()

    def _auth_headers(self, body: bytes) -> dict[str, str]:
        ts = str(int(time.time()))
        return {
            "X-SL-Token": self._token,
            "X-SL-Timestamp": ts,
            "X-SL-Signature": self._sign(ts, body),
        }

    # --------------------------------------------------------------- request
    def get(self, path: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
        """Signed GET. Returns ``(status_code, parsed_json)``.

        Non-2xx responses come back as ``(status, parsed_body)`` too so callers
        can branch on the status/body; transport-level failures raise.
        """
        url = self._base_url + path
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        headers = self._auth_headers(b"")
        req = urllib.request.Request(url, method="GET", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read()
                return resp.status, self._parse(raw)
        except urllib.error.HTTPError as err:
            raw = err.read()
            return err.code, self._parse(raw)

    def post(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        """Signed POST with a JSON body. Returns ``(status_code, parsed_json)``."""
        url = self._base_url + path
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        raw = json.dumps(body or {}, separators=(",", ":"), sort_keys=True).encode("utf-8")
        headers = self._auth_headers(raw)
        headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, method="POST", headers=headers, data=raw)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, self._parse(resp.read())
        except urllib.error.HTTPError as err:
            return err.code, self._parse(err.read())

    @staticmethod
    def _parse(raw: bytes) -> Any:
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw.decode("utf-8", errors="replace")

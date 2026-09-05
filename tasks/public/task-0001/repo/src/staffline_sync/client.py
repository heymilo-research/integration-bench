"""HTTP client for the StaffLine /svc API. See ``PROBLEM.md``."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from staffline_sync.config import Config


class StafflineError(RuntimeError):
    """Raised for an unexpected (non-2xx) HTTP response from StaffLine."""

    def __init__(self, status: int, path: str, body: Any) -> None:
        super().__init__(f"StaffLine {status} on {path}: {body!r}")
        self.status = status
        self.path = path
        self.body = body


class StafflineClient:
    """Thin wrapper over StaffLine's ``/svc`` endpoints."""

    def __init__(self, config: Config) -> None:
        self._base_url = config.vendor_base_url
        self._token = config.sl_app_token
        self._secret = config.sl_hmac_secret

    def _auth_headers(self, body: bytes) -> dict[str, str]:
        raise NotImplementedError

    def get(self, path: str, params: dict[str, Any] | None = None) -> tuple[int, Any]:
        """Signed GET. Returns ``(status_code, parsed_body)``."""
        url = self._base_url + path
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        headers = self._auth_headers(b"")
        req = urllib.request.Request(url, method="GET", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, self._parse(resp.read())
        except urllib.error.HTTPError as err:
            return err.code, self._parse(err.read())

    def post(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        """Signed POST. Returns ``(status_code, parsed_body)``."""
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

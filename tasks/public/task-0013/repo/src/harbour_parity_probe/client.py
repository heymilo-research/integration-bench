"""RecruitOS HTTP client.

OAuth2 client-credentials at ``POST /oauth/token``, ``Authorization: Bearer
<token>`` on every data-plane call, offset pagination with an authoritative
``total`` in the envelope. Retries honour ``Retry-After``.

Full vendor documentation is in ``docs/`` -- start at ``docs/index.md``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from harbour_parity_probe.config import Config

_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 6
_BASE_SLEEP_S = 0.5
_MAX_SLEEP_S = 15.0
_TOKEN_SAFETY_FRACTION = 0.2
_TOKEN_SAFETY_MAX_S = 30.0

PAGE_SIZE = 50


class VendorError(RuntimeError):
    """A call that could not be completed."""


class ApiError(RuntimeError):
    """A status RecruitOS will keep answering the same way."""

    def __init__(self, status: int, body: Any, detail: str = "") -> None:
        super().__init__(f"{status}: {detail or body}")
        self.status = status
        self.body = body


class RecruitOSClient:
    def __init__(self, cfg: Config) -> None:
        self.base_url = cfg.base_url.rstrip("/")
        self.client_id = cfg.client_id
        self.client_secret = cfg.client_secret
        self._token: str | None = None
        self._token_expires_at = 0.0
        self.requests_made = 0

    # -- auth ---------------------------------------------------------------
    def token(self) -> str:
        if self._token is not None and time.monotonic() < self._token_expires_at:
            return self._token
        form = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/oauth/token",
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        body = self._send(req)
        self._token = str(body["access_token"])
        lifetime = float(body.get("expires_in", 3600))
        margin = min(_TOKEN_SAFETY_MAX_S, lifetime * _TOKEN_SAFETY_FRACTION)
        self._token_expires_at = time.monotonic() + max(1.0, lifetime - margin)
        return self._token

    # -- transport ----------------------------------------------------------
    def _send(self, req: urllib.request.Request) -> Any:
        last: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                self.requests_made += 1
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code not in _RETRY_STATUS:
                    detail: Any = {}
                    try:
                        detail = json.load(exc)
                    except Exception:  # noqa: BLE001 - error bodies are best effort
                        pass
                    raise ApiError(exc.code, detail, f"{req.get_method()} {req.full_url}") from exc
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                delay = (
                    min(float(retry_after), _MAX_SLEEP_S)
                    if retry_after
                    else min(_BASE_SLEEP_S * (2**attempt), _MAX_SLEEP_S)
                )
                time.sleep(delay)
            except urllib.error.URLError as exc:
                last = exc
                time.sleep(min(_BASE_SLEEP_S * (2**attempt), _MAX_SLEEP_S))
        raise VendorError(f"recruitos: {req.get_method()} {req.full_url} gave up: {last}")

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}/api{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        return self._send(
            urllib.request.Request(url, headers={"Authorization": f"Bearer {self.token()}"})
        )

    # -- collections --------------------------------------------------------
    def crawl(
        self, collection: str, *, modified_since: str | None = None
    ) -> tuple[list[dict[str, Any]], int]:
        """Every row of a collection, walked to exhaustion.

        Returns ``(rows, total)`` where ``total`` is the envelope's own count
        for the filter that was applied.
        """
        rows: list[dict[str, Any]] = []
        offset = 0
        total = 0
        while True:
            params: dict[str, Any] = {"offset": offset, "limit": PAGE_SIZE}
            if modified_since:
                params["modified_since"] = modified_since
            envelope = self.get(f"/{collection}", params)
            rows.extend(list(envelope.get("data") or []))
            total = int(envelope.get("total") or 0)
            if offset + PAGE_SIZE >= total:
                return rows, total
            offset += PAGE_SIZE

    def get_by_id(self, collection: str, record_id: str) -> dict[str, Any] | None:
        """One record, or None when RecruitOS has never issued that id."""
        try:
            return self.get(f"/{collection}/{record_id}")
        except ApiError as exc:
            if exc.status == 404:
                return None
            raise

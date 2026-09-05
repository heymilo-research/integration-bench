"""Interviewly HTTP client (provided). See ``docs/`` for API details."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator

from interviewly_relay.config import Config

_TIMEOUT_S = 30.0
_MAX_RETRY_AFTER_WAITS = 5
_PAGE_SIZE = 50


class InterviewlyClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._token: str | None = None

    # -- low-level HTTP -----------------------------------------------------

    def _raw(self, method: str, url: str, *, headers: dict[str, str] | None = None,
             data: bytes | None = None) -> tuple[int, bytes]:
        req = urllib.request.Request(url, method=method, data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                return resp.getcode(), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    # -- auth -----------------------------------------------------------

    def authenticate(self) -> None:
        base = self.cfg.vendor_base_url
        form = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self.cfg.client_id,
            "client_secret": self.cfg.client_secret,
        }).encode("utf-8")
        status, body = self._raw(
            "POST", f"{base}/oauth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=form,
        )
        if status != 200:
            raise RuntimeError(f"token mint failed: {status} {body[:200]!r}")
        self._token = json.loads(body)["access_token"]

    def _ensure_token(self) -> None:
        if self._token is None:
            self.authenticate()

    # -- data plane -----------------------------------------------------

    def _get(self, path: str, *, query: dict[str, str] | None = None) -> tuple[int, bytes]:
        self._ensure_token()
        reauthed = False
        waits = 0
        while True:
            url = f"{self.cfg.vendor_base_url}{path}"
            if query:
                url += "?" + urllib.parse.urlencode(query)
            status, body = self._raw(
                "GET", url, headers={"Authorization": f"Bearer {self._token or ''}"}
            )
            if status == 429:
                if waits >= _MAX_RETRY_AFTER_WAITS:
                    raise RuntimeError(f"rate limited repeatedly on {path}")
                time.sleep(2.0)
                waits += 1
                continue
            if status == 401 and not reauthed:
                self.authenticate()
                reauthed = True
                continue
            return status, body

    def iter_collection(self, plural: str, modified_since: str | None = None) -> Iterator[dict[str, Any]]:
        """Yield every record in ``/v1/<plural>``, paging via offset+total."""
        offset = 0
        while True:
            query: dict[str, str] = {"offset": str(offset), "limit": str(_PAGE_SIZE)}
            if modified_since:
                query["modified_since"] = modified_since
            status, body = self._get(f"/v1/{plural}", query=query)
            if status != 200:
                raise RuntimeError(f"list {plural} failed: {status} {body[:200]!r}")
            page = json.loads(body)
            for rec in page.get("data", []):
                yield rec
            total = int(page.get("total", 0))
            offset += _PAGE_SIZE
            if offset >= total:
                break

    def get_one(self, plural: str, record_id: str) -> dict[str, Any] | None:
        """Fetch a single record by id, or None if it 404s."""
        status, body = self._get(f"/v1/{plural}/{record_id}")
        if status == 404:
            return None
        if status != 200:
            raise RuntimeError(f"get {plural}/{record_id}: {status} {body[:200]!r}")
        return json.loads(body)

"""RecruitOS HTTP transport (shared Sandhurst plumbing -- complete).

This is the same small client three other Sandhurst jobs use. It speaks the
API described in ``docs/`` and nothing more::

    POST /oauth/token                grant_type=client_credentials
    GET  /api/candidates?offset=&limit=
    GET  /api/jobs?offset=&limit=
    GET  /api/applications?offset=&limit=

The minted access token goes in ``Authorization: Bearer <token>`` on every
data-plane request and is re-minted when it stops being accepted.
Pagination follows ``docs/pagination.md``: ``offset``/``limit`` with the
envelope's ``total`` as the terminal condition. ``docs/index.md`` documents
the 429 rate limit and the ``Retry-After`` header; ``_request`` waits for
what the response asks for.

Records are returned EXACTLY as the wire sends them -- this layer does not
rename, reinterpret, filter or drop any field or any record. Full vendor
documentation is in ``docs/``; start at ``docs/index.md``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator

from sandhurst_rollup.config import Config

# docs/pagination.md: `limit` defaults to 50 and 50 is also the maximum.
PAGE_SIZE = 50

_MAX_ATTEMPTS = 12


class RecruitOSError(RuntimeError):
    """Any unrecoverable transport failure."""


class RecruitOSHTTPError(RecruitOSError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:200]}")
        self.status = status
        self.body = body


class RecruitOSClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._token: str | None = None

    # -- auth ---------------------------------------------------------------

    def _mint_token(self) -> str:
        payload = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self.cfg.client_id,
                "client_secret": self.cfg.client_secret,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self.cfg.vendor_base_url}/oauth/token",
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                doc = json.load(resp)
        except urllib.error.HTTPError as exc:
            raise RecruitOSHTTPError(exc.code, exc.read().decode("utf-8", "replace")) from exc
        token = doc.get("access_token")
        if not token:
            raise RecruitOSError(f"token endpoint returned no access_token: {doc}")
        self._token = str(token)
        return self._token

    def token(self) -> str:
        return self._token or self._mint_token()

    @staticmethod
    def _retry_after(headers: Any) -> float:
        """Seconds to wait after a 429, taken from the response itself."""
        raw = None
        if headers is not None:
            try:
                raw = headers.get("Retry-After")
            except AttributeError:
                raw = None
        try:
            return max(float(raw), 0.0) if raw is not None else 2.0
        except (TypeError, ValueError):
            return 2.0

    # -- requests -----------------------------------------------------------

    def _request(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.cfg.vendor_base_url}{path}"
        clean = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        if clean:
            url = f"{url}?{urllib.parse.urlencode(clean)}"

        reauthed = False
        last: Exception | None = None
        for _attempt in range(_MAX_ATTEMPTS):
            headers = {"Authorization": f"Bearer {self.token()}"}
            req = urllib.request.Request(url, headers=headers, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")
                last = exc
                if exc.code == 401 and not reauthed:
                    reauthed = True
                    self._token = None
                    self._mint_token()
                    continue
                if exc.code == 429:
                    time.sleep(self._retry_after(getattr(exc, "headers", None)))
                    continue
                raise RecruitOSHTTPError(exc.code, body) from exc
            except urllib.error.URLError as exc:
                last = exc
                time.sleep(1.0)
        raise RecruitOSError(f"GET {path} gave up after {_MAX_ATTEMPTS} attempts: {last}")

    # -- collections --------------------------------------------------------

    def pages(self, path: str) -> Iterator[dict[str, Any]]:
        """Yield each envelope of a collection walk, in order.

        docs/pagination.md: start at ``offset=0`` and keep going while
        ``offset + limit < total``.
        """
        offset = 0
        while True:
            envelope = self._request(path, params={"offset": offset, "limit": PAGE_SIZE})
            yield envelope
            total = int(envelope.get("total") or 0)
            limit = int(envelope.get("limit") or PAGE_SIZE)
            offset = int(envelope.get("offset") or offset) + limit
            if offset >= total:
                return

    def crawl(self, path: str) -> list[dict[str, Any]]:
        """Every record the collection at ``path`` serves, in wire order."""
        rows: list[dict[str, Any]] = []
        for envelope in self.pages(path):
            rows.extend(list(envelope.get("data") or []))
        return rows

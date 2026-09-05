"""Paygrade RPC transport.

Paygrade has no REST surface: every read and every write is a call to the one
endpoint, selected by a ``method=`` query parameter (``docs/index.md``,
``docs/reference.md``)::

    GET  /api/rpc?method=listEmployees&start=0&count=50
    GET  /api/rpc?method=listTombstones&since=<unix millis>
    POST /api/rpc?method=updateEmployee        (JSON body)

Written from ``docs/`` -- ``auth.md`` for the credential, ``pagination.md``
for the paging loop and the response envelope, ``reference.md`` for the method
list and the write bodies.

``call()`` hands back the response document EXACTLY as the wire sends it. This
layer does not rename, reinterpret, filter or judge any part of it -- deciding
what a response means belongs to ``closure.py``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from pg_closure_migrate.config import Config

BASE_PATH = "/api/rpc"
PAGE_SIZE = 50
_MAX_ATTEMPTS = 4


class PaygradeError(RuntimeError):
    """Any unrecoverable transport failure."""


class PaygradeHTTPError(PaygradeError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


class PaygradeClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    # -- auth ---------------------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        """The credential placement this tenant actually runs.

        Every call answers ``403`` with a body naming the header it wants; the
        credential goes there and nowhere else -- never in a query string.
        """
        return {"X-PG-Token": self.cfg.app_token}

    # -- one call -----------------------------------------------------------

    def call(
        self,
        http_method: str,
        rpc_method: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One RPC call. Returns the parsed response document, verbatim."""
        query = {"method": rpc_method}
        for key, value in (params or {}).items():
            if value is not None and value != "":
                query[key] = value
        url = f"{self.cfg.vendor_base_url}{BASE_PATH}?{urllib.parse.urlencode(query)}"

        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = self._auth_headers()
        if data is not None:
            headers["Content-Type"] = "application/json"

        last: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            req = urllib.request.Request(url, data=data, headers=headers, method=http_method)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                raise PaygradeHTTPError(exc.code, exc.read().decode("utf-8", "replace")) from exc
            except urllib.error.URLError as exc:
                # The sandbox is still coming up, or the socket blipped. Back
                # off politely rather than hammering.
                last = exc
                time.sleep(1.0 + attempt)
        raise PaygradeError(
            f"{http_method} {rpc_method} gave up after {_MAX_ATTEMPTS} attempts: {last}"
        )

    # -- reads --------------------------------------------------------------

    def list_all(self, rpc_method: str, **params: Any) -> list[dict[str, Any]]:
        """Every row of a list method, paged exactly as ``docs/pagination.md``
        describes: start at offset 0, advance by the ``count`` the server
        echoed, stop when ``more`` comes back false."""
        rows: list[dict[str, Any]] = []
        start = 0
        while True:
            doc = self.call("GET", rpc_method,
                            params={**params, "start": start, "count": PAGE_SIZE})
            page = doc["result"]
            rows.extend(page["rows"])
            if not page["more"]:
                return rows
            start = page["start"] + page["count"]

    # -- writes -------------------------------------------------------------

    def update_employee(self, payload: dict[str, Any]) -> dict[str, Any]:
        """``POST method=updateEmployee``. Body per ``docs/reference.md``; the
        response document is returned untouched."""
        return self.call("POST", "updateEmployee", body=payload)

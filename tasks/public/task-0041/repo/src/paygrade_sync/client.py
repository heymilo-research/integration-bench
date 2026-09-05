"""Paygrade RPC client. See ``PROBLEM.md``."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator

from paygrade_sync.config import Config

_TIMEOUT_S = 30.0
_PAGE_LIMIT = 50
COMPANY_ID = "acme"


class PaygradeClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._base = cfg.vendor_base_url.rstrip("/")

    # -- low-level RPC envelope (provided) -----------------------------------

    def _auth_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"X-PG-Token": self.cfg.api_token}
        if extra:
            headers.update(extra)
        return headers

    def _rpc_get(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET RPC call. Returns the parsed JSON body; raises only on transport failure."""
        query = {"method": method, **{k: str(v) for k, v in (params or {}).items() if v is not None}}
        url = f"{self._base}/api/rpc?{urllib.parse.urlencode(query)}"
        req = urllib.request.Request(url, method="GET", headers=self._auth_headers())
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body = exc.read()
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                raise RuntimeError(f"RPC {method} transport failure: {exc.code} {body[:200]!r}") from exc

    def _rpc_post(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        """POST RPC call. Returns the parsed JSON body; raises only on transport failure."""
        url = f"{self._base}/api/rpc?{urllib.parse.urlencode({'method': method})}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, method="POST", data=data,
            headers=self._auth_headers({"Content-Type": "application/json"}),
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body = exc.read()
            try:
                return json.loads(body)
            except json.JSONDecodeError:
                raise RuntimeError(f"RPC {method} transport failure: {exc.code} {body[:200]!r}") from exc

    # -- reads -------------------------------------------------

    def iter_employees(self, modified_since: int | None = None) -> Iterator[dict[str, Any]]:
        """Yield employees, optionally filtered by ``modified_since``."""
        raise NotImplementedError

    def iter_assignments(self, modified_since: int | None = None) -> Iterator[dict[str, Any]]:
        """Yield assignments, optionally filtered by ``modified_since``."""
        raise NotImplementedError

    def iter_tombstones(self, since: int) -> Iterator[dict[str, Any]]:
        """Yield tombstones with ``deleted_at >= since``."""
        raise NotImplementedError

    def get_employee(self, employee_id: str) -> dict[str, Any] | None:
        """Point-read an employee. Returns ``None`` if not found."""
        raise NotImplementedError

    def get_assignment(self, assignment_id: str) -> dict[str, Any] | None:
        """Point-read an assignment. Returns ``None`` if not found."""
        raise NotImplementedError

    # -- writes -------------------------------------------------

    def create_assignment(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create an assignment. Returns the raw parsed response body."""
        raise NotImplementedError

    def update_employee(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Update an employee. Returns the raw parsed response body."""
        raise NotImplementedError

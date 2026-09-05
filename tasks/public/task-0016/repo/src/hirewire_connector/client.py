"""HireWire HTTP client — auth + reads + writeback. See ``PROBLEM.md``."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator

from hirewire_connector.config import Config

_TIMEOUT_S = 30.0
_MAX_RETRY_AFTER_WAITS = 5


class WriteResult:
    """Outcome of a single writeback call.

    ``ok`` is True on a 2xx; ``status`` is the HTTP status; ``body`` is the
    parsed JSON body (the created/updated record on success, or the vendor's
    error body — e.g. ``{"field_errors": {...}}`` — on a validation failure).
    """

    def __init__(self, ok: bool, status: int, body: dict[str, Any]) -> None:
        self.ok = ok
        self.status = status
        self.body = body


class HireWireClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.base = cfg.vendor_base_url

    # -- low-level HTTP -----------------------------------------------------

    def _raw(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        data: bytes | None = None,
    ) -> tuple[int, bytes]:
        req = urllib.request.Request(url, method=method, data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                return resp.getcode(), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def _auth_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.cfg.api_key}"}
        if extra:
            headers.update(extra)
        return headers

    @staticmethod
    def _retry_after(_body: bytes) -> float:
        return 5.0

    def _get(self, path: str, *, query: dict[str, str] | None = None) -> tuple[int, bytes]:
        """GET against the API with the Bearer header, honoring 429 Retry-After."""
        waits = 0
        while True:
            url = f"{self.base}{path}"
            if query:
                url += "?" + urllib.parse.urlencode(query)
            status, body = self._raw("GET", url, headers=self._auth_headers())
            if status == 429:
                if waits >= _MAX_RETRY_AFTER_WAITS:
                    raise RuntimeError(f"rate limited repeatedly on GET {path}")
                time.sleep(self._retry_after(body))
                waits += 1
                continue
            return status, body

    def _write(
        self,
        method: str,
        path: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> tuple[int, bytes]:
        """POST/PATCH JSON against the API with the Bearer header (+ optional
        Idempotency-Key), honoring 429 Retry-After."""
        waits = 0
        data = json.dumps(payload).encode("utf-8")
        while True:
            headers = self._auth_headers({"Content-Type": "application/json"})
            if idempotency_key:
                headers["Idempotency-Key"] = idempotency_key
            status, body = self._raw(method, f"{self.base}{path}", headers=headers, data=data)
            if status == 429:
                if waits >= _MAX_RETRY_AFTER_WAITS:
                    raise RuntimeError(f"rate limited repeatedly on {method} {path}")
                time.sleep(self._retry_after(body))
                waits += 1
                continue
            return status, body

    # -- reads ----------------------------------------------

    def iter_candidates(self, modified_since: int | None = None) -> Iterator[dict[str, Any]]:
        """Yield every candidate, paging to exhaustion."""
        raise NotImplementedError

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        """Fetch a single candidate by id (or None if missing)."""
        raise NotImplementedError

    # -- writeback ------------------------------------------

    def patch_candidate(
        self,
        candidate_id: str,
        fields: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> WriteResult:
        """PATCH a candidate's fields. Return a :class:`WriteResult`."""
        raise NotImplementedError

    def create_event(
        self,
        candidate_id: str,
        fields: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> WriteResult:
        """POST a timeline event on a candidate. Return a :class:`WriteResult`."""
        raise NotImplementedError

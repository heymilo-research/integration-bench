"""HTTP client for TalentForge's hybrid OAuth -> session-exchange route (provided). See ``PROBLEM.md``."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator

from .config import Config

_TIMEOUT_S = 30.0
_MAX_RETRY_AFTER_WAITS = 5


class WriteResult:
    """Outcome of a single writeback call.

    ``ok`` is True on a 2xx; ``status`` is the HTTP status; ``body`` is the
    parsed JSON body (the created/updated record on success, or the vendor's
    error body -- e.g. ``{"errors": {...}}`` -- on a validation failure).
    """

    def __init__(self, ok: bool, status: int, body: dict[str, Any]) -> None:
        self.ok = ok
        self.status = status
        self.body = body


class TalentForgeClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._session_token: str | None = None
        self._rest_url: str | None = None

    # -- low-level HTTP -------------------------------------------------

    def _raw(self, method: str, url: str, *, headers: dict[str, str] | None = None,
             data: bytes | None = None) -> tuple[int, bytes]:
        req = urllib.request.Request(url, method=method, data=data, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                return resp.getcode(), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    # -- auth -------------------------------------------------------------

    def authenticate(self) -> None:
        base = self.cfg.vendor_base_url

        # 1) authorization code (auto-approved headless grant)
        q = urllib.parse.urlencode({"response_type": "code"})
        status, body = self._raw("GET", f"{base}/oauth/authorize?{q}")
        if status != 200:
            raise RuntimeError(f"authorize failed: {status} {body[:200]!r}")
        code = json.loads(body)["code"]

        # 2) token exchange (form-encoded, per docs)
        form = urllib.parse.urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.cfg.client_id,
            "client_secret": self.cfg.client_secret,
        }).encode("utf-8")
        status, body = self._raw(
            "POST", f"{base}/oauth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=form,
        )
        if status != 200:
            raise RuntimeError(f"token exchange failed: {status} {body[:200]!r}")
        tokens = json.loads(body)
        self._access_token = tokens["access_token"]
        self._refresh_token = tokens.get("refresh_token")

        # 3) session exchange -> {session_token, rest_url}. rest_url is DYNAMIC
        # (the docs' sample URL is illustrative only) -- always adopt it.
        status, body = self._raw(
            "POST", f"{base}/rest/login",
            headers={"X-TF-Access-Token": self._access_token, "Content-Type": "application/json"},
            data=b"{}",
        )
        if status != 200:
            raise RuntimeError(f"session login failed: {status} {body[:200]!r}")
        login = json.loads(body)
        self._session_token = login["session_token"]
        self._rest_url = login["rest_url"].rstrip("/")

    def _ensure_session(self) -> None:
        if self._session_token is None or self._rest_url is None:
            self.authenticate()

    # -- data plane ---------------------------------------------------------

    def _request(
        self, method: str, path: str, *, query: dict[str, str] | None = None,
        data: bytes | None = None, extra_headers: dict[str, str] | None = None,
        reauthed: bool = False,
    ) -> tuple[int, bytes]:
        self._ensure_session()
        waits = 0
        while True:
            url = f"{self._rest_url}{path}"
            if query:
                url += "?" + urllib.parse.urlencode(query)
            headers = {"X-TF-Session": self._session_token or ""}
            if extra_headers:
                headers.update(extra_headers)
            status, body = self._raw(method, url, headers=headers, data=data)
            if status == 429:
                if waits >= _MAX_RETRY_AFTER_WAITS:
                    raise RuntimeError(f"rate limited repeatedly on {method} {path}")
                time.sleep(5.0)
                waits += 1
                continue
            if status == 500 and not reauthed:
                # Session may have expired — re-authenticate once and retry.
                self._session_token = None
                self._rest_url = None
                self._ensure_session()
                return self._request(
                    method, path, query=query, data=data,
                    extra_headers=extra_headers, reauthed=True,
                )
            return status, body

    def _get(self, path: str, *, query: dict[str, str] | None = None) -> tuple[int, bytes]:
        status, body = self._request("GET", path, query=query)
        if status != 200:
            raise RuntimeError(f"GET {path} failed: {status} {body[:200]!r}")
        return status, body

    def _get_optional(self, path: str) -> tuple[int, bytes]:
        """Like ``_get`` but returns (404, body) instead of raising on a 404."""
        status, body = self._request("GET", path)
        if status not in (200, 404):
            raise RuntimeError(f"GET {path} failed: {status} {body[:200]!r}")
        return status, body

    def _write(
        self, method: str, path: str, payload: dict[str, Any], *,
        idempotency_key: str | None = None,
    ) -> tuple[int, bytes]:
        extra = {"Content-Type": "application/json"}
        if idempotency_key:
            extra["Idempotency-Key"] = idempotency_key
        data = json.dumps(payload).encode("utf-8")
        return self._request(method, path, data=data, extra_headers=extra)

    # -- list pagination ------------------------------------------------

    def _iter_pages(self, path: str, modified_since: str | None) -> Iterator[dict[str, Any]]:
        """Page a cursor-paginated list endpoint to exhaustion."""
        cursor: str | None = None
        while True:
            query: dict[str, str] = {}
            if cursor:
                query["cursor"] = cursor
            if modified_since:
                query["modified_since"] = modified_since
            _, body = self._get(path, query=query)
            page = json.loads(body)
            for rec in page.get("data", []):
                yield rec
            if not page.get("cursor"):
                break
            cursor = page["cursor"]

    def iter_candidates(self, modified_since: str | None = None) -> Iterator[dict[str, Any]]:
        yield from self._iter_pages("/candidates", modified_since)

    def iter_applications(self, modified_since: str | None = None) -> Iterator[dict[str, Any]]:
        yield from self._iter_pages("/applications", modified_since)

    # -- get-by-id ---------------------------------------------------------

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        status, body = self._get_optional(f"/candidates/{candidate_id}")
        if status == 404:
            return None
        return json.loads(body)

    def get_application(self, application_id: str) -> dict[str, Any] | None:
        status, body = self._get_optional(f"/applications/{application_id}")
        if status == 404:
            return None
        return json.loads(body)

    # -- writeback --------------------------------------------------------

    def create_candidate(
        self, fields: dict[str, Any], *, idempotency_key: str | None = None,
    ) -> WriteResult:
        status, body = self._write("POST", "/candidates", fields, idempotency_key=idempotency_key)
        parsed = json.loads(body) if body else {}
        return WriteResult(ok=200 <= status < 300, status=status, body=parsed)

    def update_candidate(
        self, candidate_id: str, fields: dict[str, Any], *, idempotency_key: str | None = None,
    ) -> WriteResult:
        status, body = self._write(
            "PATCH", f"/candidates/{candidate_id}", fields, idempotency_key=idempotency_key
        )
        parsed = json.loads(body) if body else {}
        return WriteResult(ok=200 <= status < 300, status=status, body=parsed)

    def create_note(
        self, candidate_id: str, fields: dict[str, Any], *, idempotency_key: str | None = None,
    ) -> WriteResult:
        status, body = self._write(
            "POST", f"/candidates/{candidate_id}/notes", fields, idempotency_key=idempotency_key
        )
        parsed = json.loads(body) if body else {}
        return WriteResult(ok=200 <= status < 300, status=status, body=parsed)

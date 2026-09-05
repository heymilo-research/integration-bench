"""HTTP client for TalentLoop's OAuth client-credentials + cursor-paginated REST API (provided). See ``PROBLEM.md``."""

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
_MAX_5XX_RETRIES = 6


class GetResult:
    """Outcome of a get-by-id call: ``status`` is one of "ok"/"gone"/"not_found"."""

    def __init__(self, status: str, record: dict[str, Any] | None) -> None:
        self.status = status
        self.record = record

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def gone(self) -> bool:
        return self.status == "gone"

    @property
    def not_found(self) -> bool:
        return self.status == "not_found"


class WriteResult:
    """Outcome of a single writeback call (used by summit/writeback tasks)."""

    def __init__(self, ok: bool, status: int, body: dict[str, Any]) -> None:
        self.ok = ok
        self.status = status
        self.body = body


class TalentLoopClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._access_token: str | None = None
        self._token_issued_monotonic: float | None = None

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
        form = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self.cfg.client_id,
            "client_secret": self.cfg.client_secret,
        }).encode("utf-8")
        status, body = self._raw(
            "POST", f"{base}/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=form,
        )
        if status != 200:
            raise RuntimeError(f"token request failed: {status} {body[:200]!r}")
        payload = json.loads(body)
        self._access_token = payload["access_token"]
        self._token_issued_monotonic = time.monotonic()

    def _ensure_token(self) -> None:
        if self._access_token is None:
            self.authenticate()

    # -- data plane ---------------------------------------------------------

    def _request(
        self, method: str, path: str, *, query: dict[str, str] | None = None,
        data: bytes | None = None, extra_headers: dict[str, str] | None = None,
        reauthed: bool = False,
    ) -> tuple[int, bytes]:
        self._ensure_token()
        waits = 0
        five_xx_retries = 0
        while True:
            url = f"{self.cfg.vendor_base_url}{path}"
            if query:
                url += "?" + urllib.parse.urlencode(query)
            headers = {"Authorization": f"Bearer {self._access_token}"}
            if extra_headers:
                headers.update(extra_headers)
            status, body = self._raw(method, url, headers=headers, data=data)
            if status == 429:
                if waits >= _MAX_RETRY_AFTER_WAITS:
                    raise RuntimeError(f"rate limited repeatedly on {method} {path}")
                time.sleep(5.0)
                waits += 1
                continue
            if status == 401 and not reauthed:
                # Token expired/invalid -- re-authenticate once and retry the
                # SAME request (never restart pagination from page 1: the
                # caller re-issues this exact query, cursor included).
                self._access_token = None
                self._ensure_token()
                return self._request(
                    method, path, query=query, data=data,
                    extra_headers=extra_headers, reauthed=True,
                )
            if status >= 500 and five_xx_retries < _MAX_5XX_RETRIES:
                five_xx_retries += 1
                time.sleep(min(0.25 * (2 ** five_xx_retries), 4.0))
                continue
            return status, body

    def _get(self, path: str, *, query: dict[str, str] | None = None) -> tuple[int, bytes]:
        status, body = self._request("GET", path, query=query)
        if status != 200:
            raise RuntimeError(f"GET {path} failed: {status} {body[:200]!r}")
        return status, body

    def _get_tristate(self, path: str) -> GetResult:
        status, body = self._request("GET", path)
        if status == 200:
            return GetResult("ok", json.loads(body))
        if status == 410:
            return GetResult("gone", None)
        if status == 404:
            return GetResult("not_found", None)
        raise RuntimeError(f"GET {path} failed: {status} {body[:200]!r}")

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
            cursor = page.get("cursor")
            if not cursor:
                break

    def iter_candidates(self, modified_since: str | None = None) -> Iterator[dict[str, Any]]:
        yield from self._iter_pages("/candidates", modified_since)

    def iter_jobs(self, modified_since: str | None = None) -> Iterator[dict[str, Any]]:
        yield from self._iter_pages("/jobs", modified_since)

    def iter_applications(self, modified_since: str | None = None) -> Iterator[dict[str, Any]]:
        yield from self._iter_pages("/applications", modified_since)

    def iter_notes(self, modified_since: str | None = None) -> Iterator[dict[str, Any]]:
        yield from self._iter_pages("/notes", modified_since)

    # -- get-by-id (the 410-vs-404 reconcile primitive) --------------------

    def get_candidate(self, candidate_id: str) -> GetResult:
        return self._get_tristate(f"/candidates/{candidate_id}")

    def get_job(self, job_id: str) -> GetResult:
        return self._get_tristate(f"/jobs/{job_id}")

    def get_application(self, application_id: str) -> GetResult:
        return self._get_tristate(f"/applications/{application_id}")

    def get_note(self, note_id: str) -> GetResult:
        return self._get_tristate(f"/notes/{note_id}")

    # -- writeback ----------------------------------------------------------

    def create_note(
        self, candidate_id: str, fields: dict[str, Any], *, idempotency_key: str | None = None,
    ) -> WriteResult:
        status, body = self._write(
            "POST", f"/candidates/{candidate_id}/notes", fields, idempotency_key=idempotency_key
        )
        parsed = json.loads(body) if body else {}
        return WriteResult(ok=200 <= status < 300, status=status, body=parsed)

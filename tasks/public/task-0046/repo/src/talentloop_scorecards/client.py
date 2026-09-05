"""TalentLoop HTTP client.

OAuth client-credentials, cursor pagination, JSON everywhere. The token is
minted on first use and re-minted when it lapses or when the platform answers
``401``; ``expires_in`` on the token response is authoritative.

    POST /token                              mint a bearer token
    GET  /candidates?cursor=<c>              cursor-paginated collection
    GET  /candidates/{id}                    one candidate
    POST /candidates/{id}/notes              create a note
    POST /notes/{id}/attachments             upload a file against a note
    GET  /notes/{id}/attachments             a note's attachments

Every data-plane call carries ``Authorization: Bearer <token>``. Full vendor
documentation is in ``docs/``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from talentloop_scorecards.config import Config

_RETRY_STATUS = {500, 502, 503, 504}
_MAX_ATTEMPTS = 5
_BACKOFF_S = 0.4
_MAX_BACKOFF_S = 6.0
# Re-mint this many seconds before the token's stated expiry rather than waiting
# for the 401, so a long pass does not trip over its own credential.
_TOKEN_MARGIN_S = 30.0


class TalentLoopError(RuntimeError):
    """Transport-level failure talking to TalentLoop."""


class TalentLoopHTTPError(TalentLoopError):
    """A non-2xx response. ``status`` and ``payload`` carry what came back."""

    def __init__(self, method: str, path: str, status: int, payload: Any) -> None:
        super().__init__(f"{method} {path} -> {status}: {payload!r}")
        self.method = method
        self.path = path
        self.status = status
        self.payload = payload


class TalentLoopClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._token: str | None = None
        self._token_expires_at = 0.0
        self.requests_made = 0

    # -- auth ---------------------------------------------------------------
    def _mint_token(self) -> str:
        body = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self.cfg.client_id,
            "client_secret": self.cfg.client_secret,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{self.cfg.vendor_base_url}/token",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                payload = json.load(resp)
        except urllib.error.HTTPError as exc:
            raise TalentLoopError(f"POST /token -> {exc.code}") from exc
        self._token = payload["access_token"]
        self._token_expires_at = time.monotonic() + float(payload.get("expires_in", 3600))
        return self._token

    def _bearer(self) -> str:
        if self._token is None or time.monotonic() >= self._token_expires_at - _TOKEN_MARGIN_S:
            return self._mint_token()
        return self._token

    # -- transport ----------------------------------------------------------
    def _call(self, method: str, path: str, *, params: dict[str, Any] | None = None,
              body: dict[str, Any] | None = None,
              idempotency_key: str | None = None) -> Any:
        url = f"{self.cfg.vendor_base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        payload = json.dumps(body).encode("utf-8") if body is not None else None

        reauthed = False
        for attempt in range(_MAX_ATTEMPTS):
            headers = {"Authorization": f"Bearer {self._bearer()}"}
            if payload is not None:
                headers["Content-Type"] = "application/json"
            if idempotency_key:
                headers["Idempotency-Key"] = idempotency_key
            req = urllib.request.Request(url, data=payload, headers=headers, method=method)
            self.requests_made += 1
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else None
            except urllib.error.HTTPError as exc:
                parsed: Any = None
                try:
                    parsed = json.load(exc)
                except Exception:  # noqa: BLE001 - error bodies are not always JSON
                    parsed = None
                if exc.code == 401 and not reauthed:
                    # The token lapsed mid-pass: mint a fresh one and retry once.
                    reauthed = True
                    self._token = None
                    continue
                if exc.code in _RETRY_STATUS and attempt < _MAX_ATTEMPTS - 1:
                    time.sleep(min(_BACKOFF_S * (2 ** attempt), _MAX_BACKOFF_S))
                    continue
                raise TalentLoopHTTPError(method, path, exc.code, parsed) from exc
            except urllib.error.URLError as exc:
                if attempt < _MAX_ATTEMPTS - 1:
                    time.sleep(min(_BACKOFF_S * (2 ** attempt), _MAX_BACKOFF_S))
                    continue
                raise TalentLoopError(f"{method} {path}: {exc}") from exc
        raise TalentLoopError(f"{method} {path}: exhausted {_MAX_ATTEMPTS} attempts")

    # -- reads --------------------------------------------------------------
    def crawl(self, collection: str) -> list[dict[str, Any]]:
        """Every record in a collection, walked with the cursor to exhaustion."""
        rows: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params = {"cursor": cursor} if cursor else {}
            envelope = self._call("GET", f"/{collection}", params=params) or {}
            rows.extend(envelope.get("data") or [])
            cursor = envelope.get("cursor")
            if cursor is None:
                return rows

    def get_candidate(self, candidate_id: str) -> dict[str, Any]:
        return self._call("GET", f"/candidates/{candidate_id}")

    def list_attachments(self, note_id: str) -> list[dict[str, Any]]:
        envelope = self._call("GET", f"/notes/{note_id}/attachments") or {}
        return list(envelope.get("data") or [])

    # -- writes -------------------------------------------------------------
    def create_note(self, candidate_id: str, body: str, author: str,
                    *, idempotency_key: str | None = None) -> dict[str, Any]:
        return self._call(
            "POST", f"/candidates/{candidate_id}/notes",
            body={"body": body, "author": author},
            idempotency_key=idempotency_key,
        )

    def upload_attachment(self, note_id: str, payload: dict[str, Any],
                          *, idempotency_key: str | None = None) -> dict[str, Any]:
        """Upload one file against a note. Returns the platform's response body."""
        return self._call(
            "POST", f"/notes/{note_id}/attachments",
            body=payload,
            idempotency_key=idempotency_key,
        )

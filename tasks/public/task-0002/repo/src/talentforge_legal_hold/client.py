"""TalentForge HTTP transport. See the vendor documentation in ``docs/``."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from talentforge_legal_hold.config import Config

_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 6
_MAX_RETRY_SLEEP_S = 10.0


class TalentForgeError(RuntimeError):
    pass


class TalentForgeClient:
    """One authenticated conversation with a TalentForge tenant."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.session_token: str | None = None
        self.rest_url: str | None = None
        self.requests_made = 0

    # -- handshake ---------------------------------------------------------
    def login(self) -> None:
        """Run the whole handshake and hold the resulting session."""
        # The authorize step is deliberately called WITHOUT the client id in the
        # query string: a configured credential belongs in the token-exchange
        # body, not in a URL that lands in every access log along the way. This
        # tenant's consent screen is headless and auto-approves.
        code = self._json(
            "GET",
            f"{self.cfg.vendor_base_url}/oauth/authorize?response_type=code",
        )["code"]
        tokens = self._json(
            "POST",
            f"{self.cfg.vendor_base_url}/oauth/token",
            body={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self.cfg.client_id,
                "client_secret": self.cfg.client_secret,
            },
        )
        session = self._json(
            "POST",
            f"{self.cfg.vendor_base_url}/rest/login",
            body={},
            headers={"X-TF-Access-Token": tokens["access_token"]},
        )
        self.session_token = session["session_token"]
        # The data-plane base URL is whatever login handed back, not a constant.
        self.rest_url = str(session["rest_url"]).rstrip("/")

    def _ensure_session(self) -> None:
        if self.session_token is None or self.rest_url is None:
            self.login()

    # -- one data-plane read ------------------------------------------------
    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET one data-plane path (e.g. ``/candidates``) and return its body.

        ``path`` is relative to the session's own base URL. ``params`` are
        url-encoded; ``None`` values are dropped so callers can pass an absent
        cursor without special-casing it.
        """
        self._ensure_session()
        query = {k: v for k, v in (params or {}).items() if v is not None}
        url = f"{self.rest_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"

        reauthed = False
        last: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                body = self._json("GET", url, headers={"X-TF-Session": self.session_token or ""})
                self.requests_made += 1
                return body
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code not in _RETRY_STATUS:
                    raise TalentForgeError(f"GET {path} -> {exc.code}") from exc
                if exc.code == 429:
                    retry_after = (exc.headers or {}).get("Retry-After")
                    time.sleep(min(float(retry_after or 1.0), _MAX_RETRY_SLEEP_S))
                    continue
                # A dead session is surfaced by this data plane as a 5xx. Mint a
                # new one once; a second 5xx is a real server fault, so back off
                # instead of hammering.
                if not reauthed:
                    reauthed = True
                    self.login()
                    url = f"{self.rest_url}{path}" + (f"?{urllib.parse.urlencode(query)}" if query else "")
                    continue
                time.sleep(min(1.0 * (2**attempt), _MAX_RETRY_SLEEP_S))
        raise TalentForgeError(f"GET {path} gave up after {_MAX_ATTEMPTS} attempts: {last}")

    # -- raw json helper -----------------------------------------------------
    @staticmethod
    def _json(
        method: str,
        url: str,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req_headers = dict(headers or {})
        if data is not None:
            req_headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)

"""TalentForge transport -- login, cursor walk, get-by-id.

The auth chain is the
three-step hybrid route docs/auth.md describes (authorize -> token ->
session), and ``rest_url`` is read fresh out of the login response every time
rather than being hardcoded, per that page's warning.

``pages()`` follows the ``cursor`` envelope to exhaustion and re-sends the
caller's query parameters on every page, so a filtered walk stays filtered all
the way down.

Retries: a 429 waits out the advertised ``Retry-After``; a 5xx on a
previously-good session triggers exactly one full re-login before the call is
retried (docs/auth.md: an expired session surfaces as 500, not 401).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator

from tf_event_cutover.config import Config

_MAX_ATTEMPTS = 4
_DEFAULT_BACKOFF_S = 0.2
_MAX_BACKOFF_S = 2.0


class TalentForgeError(RuntimeError):
    pass


class TalentForgeClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.access_token: str | None = None
        self.refresh_token: str | None = None
        self.session_token: str | None = None
        self.rest_url: str | None = None
        self.gets = 0

    # -- auth ---------------------------------------------------------------

    def ensure_session(self) -> None:
        if self.session_token and self.rest_url:
            return
        self._full_login()

    def _full_login(self) -> None:
        code = self._json_request(
            "GET", f"{self.cfg.vendor_base_url}/oauth/authorize?response_type=code"
        )["code"]
        token = self._json_request(
            "POST",
            f"{self.cfg.vendor_base_url}/oauth/token",
            body={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": self.cfg.client_id,
                "client_secret": self.cfg.client_secret,
            },
        )
        self.access_token = token["access_token"]
        self.refresh_token = token["refresh_token"]
        session = self._json_request(
            "POST",
            f"{self.cfg.vendor_base_url}/rest/login",
            headers={"X-TF-Access-Token": self.access_token},
        )
        self.session_token = session["session_token"]
        # The data-plane base URL is whatever login handed back, not a constant.
        self.rest_url = str(session["rest_url"]).rstrip("/")

    # -- data plane ---------------------------------------------------------

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """One GET against the session's data-plane base URL."""
        self.ensure_session()
        url = f"{self.rest_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        self.gets += 1
        return self._data_request("GET", url)

    def record(self, collection: str, record_id: str) -> dict[str, Any]:
        """Fetch a single record by its exact id."""
        return self.get(f"/{collection}/{record_id}")

    def pages(
        self, collection: str, params: dict[str, Any] | None = None
    ) -> Iterator[list[dict[str, Any]]]:
        """Yield each page's ``data`` array, following the cursor to exhaustion."""
        cursor: str | None = None
        base = dict(params or {})
        while True:
            query = dict(base)
            if cursor:
                query["cursor"] = cursor
            envelope = self.get(f"/{collection}", query)
            yield envelope.get("data", [])
            cursor = envelope.get("cursor")
            if not cursor:
                return

    def crawl(
        self, collection: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for page in self.pages(collection, params):
            rows.extend(page)
        return rows

    # -- plumbing -----------------------------------------------------------

    def _data_request(self, method: str, url: str) -> dict[str, Any]:
        relogin_used = False
        for attempt in range(_MAX_ATTEMPTS):
            headers = {"X-TF-Session": self.session_token or ""}
            try:
                return self._json_request(method, url, headers=headers)
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    retry_after = (exc.headers or {}).get("Retry-After")
                    delay = float(retry_after) if retry_after else _DEFAULT_BACKOFF_S
                    time.sleep(min(delay, _MAX_BACKOFF_S))
                    continue
                if exc.code in (401, 500) and not relogin_used:
                    relogin_used = True
                    self._full_login()
                    continue
                raise TalentForgeError(f"{method} {url} -> {exc.code}") from exc
            except urllib.error.URLError as exc:
                if attempt == _MAX_ATTEMPTS - 1:
                    raise TalentForgeError(f"{method} {url}: {exc}") from exc
                time.sleep(min(_DEFAULT_BACKOFF_S * (2**attempt), _MAX_BACKOFF_S))
        raise TalentForgeError(f"{method} {url}: exhausted {_MAX_ATTEMPTS} attempts")

    @staticmethod
    def _json_request(
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = None
        hdrs = dict(headers or {})
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)

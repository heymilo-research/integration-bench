"""HTTP clients for the two vendors.

Both platforms speak the same dialect: OAuth2 client-credentials at
``POST /oauth/token``, ``Authorization: Bearer <token>`` on every data-plane
call, offset pagination with an authoritative ``total`` in the envelope.

    RecruitOS   /api/candidates, /api/jobs, /api/applications      (read)
    Placemint   /api/placements, /api/clients, /api/notes          (read+write)

Full vendor documentation is in ``docs/`` -- start at ``docs/index.md``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from northgate_placement_sync.config import Config

_RETRY_STATUS = {429, 500, 502, 503, 504}

# A throttle is the platform telling us when to come back, not an error: the
# interval comes from Retry-After rather than a fixed guess.
_MAX_ATTEMPTS = 6
_BASE_SLEEP_S = 0.5
_MAX_SLEEP_S = 15.0
# Mint a fresh token a little before the advertised expiry so a long cycle never
# races the boundary. The margin is a FRACTION of the advertised lifetime, not a
# fixed number of seconds: the two platforms hand out very differently sized
# tokens, and a fixed margin wide enough for the long-lived one re-mints on
# every single call against the short-lived one.
_TOKEN_SAFETY_FRACTION = 0.2
_TOKEN_SAFETY_MAX_S = 30.0


class VendorError(RuntimeError):
    """A call that could not be completed."""


class ApiError(RuntimeError):
    """A call the platform answered with a non-retryable error status."""

    def __init__(self, status: int, body: Any, detail: str = "") -> None:
        super().__init__(f"{status}: {detail or body}")
        self.status = status
        self.body = body


class VendorClient:
    """One authenticated session against one platform."""

    def __init__(
        self,
        name: str,
        base_url: str,
        base_path: str,
        client_id: str,
        client_secret: str,
        page_size: int,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.base_path = base_path
        self.client_id = client_id
        self.client_secret = client_secret
        self.page_size = page_size
        self._token: str | None = None
        self._token_expires_at = 0.0
        self.requests_made = 0

    # -- auth ---------------------------------------------------------------
    def token(self) -> str:
        """A valid access token, minted on demand and reused until it expires."""
        if self._token is not None and time.monotonic() < self._token_expires_at:
            return self._token
        form = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/oauth/token",
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        _status, body = self._send(req)
        self._token = str(body["access_token"])
        lifetime = float(body.get("expires_in", 3600))
        margin = min(_TOKEN_SAFETY_MAX_S, lifetime * _TOKEN_SAFETY_FRACTION)
        self._token_expires_at = time.monotonic() + max(1.0, lifetime - margin)
        return self._token

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token()}"}

    # -- transport ----------------------------------------------------------
    def _send(self, req: urllib.request.Request) -> tuple[int, Any]:
        last: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                self.requests_made += 1
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                    return resp.getcode(), (json.loads(raw) if raw else {})
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code not in _RETRY_STATUS:
                    detail: Any = {}
                    try:
                        detail = json.load(exc)
                    except Exception:  # noqa: BLE001 - error bodies are best effort
                        pass
                    raise ApiError(exc.code, detail, f"{req.get_method()} {req.full_url}") from exc
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                if retry_after:
                    delay = min(float(retry_after), _MAX_SLEEP_S)
                else:
                    delay = min(_BASE_SLEEP_S * (2**attempt), _MAX_SLEEP_S)
                time.sleep(delay)
            except urllib.error.URLError as exc:
                last = exc
                time.sleep(min(_BASE_SLEEP_S * (2**attempt), _MAX_SLEEP_S))
        raise VendorError(f"{self.name}: {req.get_method()} {req.full_url} gave up: {last}")

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{self.base_path}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        _status, body = self._send(urllib.request.Request(url, headers=self._auth_headers()))
        return body

    # -- pagination ---------------------------------------------------------
    def list_page(
        self,
        collection: str,
        *,
        offset: int,
        limit: int,
        modified_since: str | None = None,
    ) -> dict[str, Any]:
        """One page envelope of a collection, verbatim."""
        params: dict[str, Any] = {"offset": offset, "limit": limit}
        if modified_since is not None:
            params["modified_since"] = modified_since
        return self.get(f"/{collection}", params)

    def crawl(
        self, collection: str, *, modified_since: str | None = None
    ) -> list[dict[str, Any]]:
        """Every row of a collection, walked to exhaustion.

        ``total`` is authoritative on both platforms, so the walk advances
        ``offset`` by ``limit`` until ``offset + limit >= total``.
        """
        rows: list[dict[str, Any]] = []
        offset = 0
        limit = self.page_size
        while True:
            envelope = self.list_page(
                collection, offset=offset, limit=limit, modified_since=modified_since
            )
            rows.extend(list(envelope.get("data") or []))
            total = int(envelope.get("total") or 0)
            if offset + limit >= total:
                return rows
            offset += limit


class RecruitOSClient(VendorClient):
    """RecruitOS: ``/api``, candidates / jobs / applications. Read-only for us."""

    PAGE_SIZE = 50

    def __init__(self, cfg: Config) -> None:
        super().__init__(
            "recruitos",
            cfg.recruitos_base_url,
            "/api",
            cfg.ro_client_id,
            cfg.ro_client_secret,
            self.PAGE_SIZE,
        )

    def applications(self, *, modified_since: str | None = None) -> list[dict[str, Any]]:
        return self.crawl("applications", modified_since=modified_since)


class PlacemintClient(VendorClient):
    """Placemint: ``/api``, placements / clients / notes. We write here."""

    PAGE_SIZE = 100

    def __init__(self, cfg: Config) -> None:
        super().__init__(
            "placemint",
            cfg.placemint_base_url,
            "/api",
            cfg.pm_client_id,
            cfg.pm_client_secret,
            self.PAGE_SIZE,
        )

    def placements(self, *, modified_since: str | None = None) -> list[dict[str, Any]]:
        return self.crawl("placements", modified_since=modified_since)

    def patch_placement(
        self, placement_id: str, fields: dict[str, Any], *, idempotency_key: str
    ) -> tuple[int, Any]:
        """``PATCH /api/placements/{id}``. Returns ``(status, body)``.

        Raises ``ApiError`` for a status the platform will keep answering the
        same way (404, 422, ...); retryable transport failures are handled
        inside ``_send``.
        """
        url = f"{self.base_url}/api/placements/{placement_id}"
        headers = {
            **self._auth_headers(),
            "Content-Type": "application/json",
            "Idempotency-Key": idempotency_key,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(fields).encode("utf-8"),
            headers=headers,
            method="PATCH",
        )
        return self._send(req)

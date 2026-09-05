"""Vettly HTTP transport.

This is the half of the cutover the platform team has already done: the grant
that used to live inside vaultd is minted and maintained here, in-process.
Written against ``docs/``: the OAuth client-credentials flow and the single-use
refresh rotation from ``auth.md``, the cursor loop from ``pagination.md``, the
collection paths from ``entities.md``.

    POST /oauth/token                      grant_type=client_credentials
    POST /oauth/token                      grant_type=refresh_token
    GET  /v1/subjects|checks|reports       ?cursor=...&modified_since=...
    GET  /v1/subjects|checks|reports/{id}

``crawl()``, ``pages()`` and ``get_record()`` return records exactly as Vettly
sends them -- no field renaming, no type coercion, no filtering, and whatever
``modified_since`` the caller hands over goes on the query string verbatim.
What a record means, and what a watermark is spelled like, are the caller's
problem and not this transport's.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator

from vaultd_cutover.config import Config

_RETRY_STATUS = {429, 500, 502, 503, 504}
# A throttle is the vendor telling us when to come back, not an error: the
# allowance has to be able to outlast a quota window, so it is generous in
# attempts and takes its interval from Retry-After rather than a fixed guess.
_MAX_ATTEMPTS = 6
_BACKOFF_S = 0.5
_MAX_BACKOFF_S = 90.0
# docs/auth.md §2: tokens advertise 120s and die 45s early. Renew with margin.
_EXPIRY_MARGIN_S = 50.0


class VettlyError(RuntimeError):
    pass


class VettlyAuthError(VettlyError):
    pass


class VettlyNotFound(VettlyError):
    """The vendor answered 404 for a record we asked for by id."""


class VettlyClient:
    """Token lifecycle plus Vettly's read surface, for one caller."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.base_url = cfg.vettly_base_url
        self._access_token: str | None = None
        self._refresh_token: str | None = None
        self._expires_at: float = 0.0
        self.list_requests = 0
        self.record_requests = 0

    # -- token lifecycle ----------------------------------------------------

    def _post_token(self, form: dict[str, str]) -> dict[str, Any]:
        data = urllib.parse.urlencode(form).encode("utf-8")
        url = f"{self.base_url}/oauth/token"
        last: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            req = urllib.request.Request(
                url,
                data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code not in _RETRY_STATUS:
                    raise VettlyAuthError(
                        f"POST /oauth/token ({form.get('grant_type')}) -> "
                        f"{exc.code}: {_read_error_body(exc)}"
                    ) from exc
                time.sleep(_delay_for(exc, attempt))
            except urllib.error.URLError as exc:
                last = exc
                time.sleep(_BACKOFF_S * (2 ** attempt))
        raise VettlyAuthError(
            f"POST /oauth/token gave up after {_MAX_ATTEMPTS} attempts: {last}")

    def _mint(self) -> None:
        payload = self._post_token({
            "grant_type": "client_credentials",
            "client_id": self.cfg.vt_client_id,
            "client_secret": self.cfg.vt_client_secret,
        })
        self._adopt(payload)

    def _refresh(self) -> bool:
        """Rotate the refresh chain. False means the chain is no longer usable."""
        if not self._refresh_token:
            return False
        try:
            payload = self._post_token({
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
            })
        except VettlyAuthError:
            self._access_token = None
            self._refresh_token = None
            return False
        self._adopt(payload)
        return True

    def _adopt(self, payload: dict[str, Any]) -> None:
        self._access_token = payload.get("access_token")
        self._refresh_token = payload.get("refresh_token") or self._refresh_token
        expires_in = float(payload.get("expires_in") or 0)
        self._expires_at = time.monotonic() + max(0.0, expires_in - _EXPIRY_MARGIN_S)

    def _authorize(self) -> str:
        if self._access_token and time.monotonic() < self._expires_at:
            return self._access_token
        if self._refresh_token and self._refresh():
            return self._access_token or ""
        self._mint()
        return self._access_token or ""

    def _reauthorize(self) -> None:
        """Called after a 401: the token we were holding is no longer good."""
        self._access_token = None
        self._expires_at = 0.0
        if self._refresh_token and self._refresh():
            return
        self._mint()

    # -- data plane ---------------------------------------------------------

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        last: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            token = self._authorize()
            req = urllib.request.Request(
                url, headers={"Authorization": f"Bearer {token}"})
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code == 401:
                    self._reauthorize()
                    continue
                if exc.code == 404:
                    raise VettlyNotFound(f"GET {path} -> 404") from exc
                if exc.code not in _RETRY_STATUS:
                    raise VettlyError(
                        f"GET {path} -> {exc.code}: {_read_error_body(exc)}") from exc
                time.sleep(_delay_for(exc, attempt))
            except urllib.error.URLError as exc:
                last = exc
                time.sleep(_BACKOFF_S * (2 ** attempt))
        raise VettlyError(f"GET {path} gave up after {_MAX_ATTEMPTS} attempts: {last}")

    def list_page(
        self,
        collection: str,
        *,
        cursor: str | None = None,
        modified_since: Any = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if cursor:
            params["cursor"] = cursor
        if modified_since is not None:
            params["modified_since"] = modified_since
        envelope = self._get(f"/v1/{collection}", params)
        self.list_requests += 1
        return envelope

    def pages(
        self, collection: str, *, modified_since: Any = None,
    ) -> Iterator[list[dict[str, Any]]]:
        """Yield each page's ``data`` array, following the cursor to the end.

        ``modified_since`` rides the first request of the cycle only, which is
        what ``docs/pagination.md`` describes; the cursor carries the rest.
        """
        cursor: str | None = None
        first = True
        while True:
            envelope = self.list_page(
                collection,
                cursor=cursor,
                modified_since=modified_since if first else None,
            )
            first = False
            yield list(envelope.get("data") or [])
            cursor = envelope.get("cursor")
            if not cursor:
                return

    def crawl(
        self, collection: str, *, modified_since: Any = None,
    ) -> list[dict[str, Any]]:
        """Every record the collection returns for this call, raw."""
        rows: list[dict[str, Any]] = []
        for batch in self.pages(collection, modified_since=modified_since):
            rows.extend(batch)
        return rows

    def get_record(self, collection: str, record_id: str) -> dict[str, Any] | None:
        """One record by id, or ``None`` when Vettly answers 404."""
        try:
            record = self._get(f"/v1/{collection}/{record_id}", {})
        except VettlyNotFound:
            return None
        self.record_requests += 1
        return record


def _read_error_body(exc: urllib.error.HTTPError) -> Any:
    try:
        return json.load(exc)
    except Exception:  # noqa: BLE001 - diagnostics only
        return {}


def _delay_for(exc: urllib.error.HTTPError, attempt: int) -> float:
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    if retry_after:
        try:
            return min(float(retry_after) + 0.5, _MAX_BACKOFF_S)
        except (TypeError, ValueError):
            pass
    return min(_BACKOFF_S * (2 ** attempt), _MAX_BACKOFF_S)

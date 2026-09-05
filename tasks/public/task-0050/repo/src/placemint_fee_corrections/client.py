"""Placemint HTTP transport. See the vendor documentation in ``docs/``."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from placemint_fee_corrections.config import Config

_MAX_ATTEMPTS = 6
_BACKOFF_S = 0.4
_MAX_BACKOFF_S = 20.0
_REFRESH_MARGIN_S = 8.0


class PlacemintError(RuntimeError):
    pass


class PlacemintClient:
    """Thin transport. Counts what it did so the caller can report on it."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.placements_updated = 0
        self.notes_created = 0
        self.token_mints = 0
        self._token: str | None = None
        self._token_expires_at = 0.0

    # -- auth --------------------------------------------------------------
    def _mint(self) -> str:
        body = urllib.parse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self.cfg.client_id,
            "client_secret": self.cfg.client_secret,
        }).encode("utf-8")
        url = f"{self.cfg.vendor_base_url}/oauth/token"
        for attempt in range(_MAX_ATTEMPTS):
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    payload = json.load(resp)
                break
            except urllib.error.HTTPError as exc:
                if exc.code != 429:
                    raise PlacemintError(f"token mint -> {exc.code}") from exc
                self._sleep_for(exc, attempt)
            except urllib.error.URLError:
                time.sleep(_BACKOFF_S * (2 ** attempt))
        else:
            raise PlacemintError("token mint: gave up")

        self._token = payload["access_token"]
        self._token_expires_at = time.monotonic() + float(payload.get("expires_in", 60))
        self.token_mints += 1
        return self._token

    def _bearer(self) -> str:
        if self._token is None or time.monotonic() >= self._token_expires_at - _REFRESH_MARGIN_S:
            return self._mint()
        return self._token

    @staticmethod
    def _sleep_for(exc: urllib.error.HTTPError, attempt: int) -> None:
        """Wait before retrying. ``Retry-After`` is taken literally.

        Placemint's ``Retry-After`` is a real number of seconds, not advice
        (``docs/index.md``), so it is NOT clamped: shortening it just earns
        another 429. Only the fallback exponential backoff, used when the
        response carried no header at all, is capped.
        """
        retry_after = exc.headers.get("Retry-After") if exc.headers else None
        try:
            delay = float(retry_after) if retry_after else None
        except (TypeError, ValueError):
            delay = None
        if delay is None:
            delay = min(_BACKOFF_S * (2 ** attempt), _MAX_BACKOFF_S)
        time.sleep(delay)

    # -- plumbing ----------------------------------------------------------
    def _request(self, method: str, path: str, *,
                 params: dict[str, Any] | None = None,
                 body: dict[str, Any] | None = None,
                 idempotency_key: str | None = None) -> tuple[int, Any]:
        """Returns ``(status, decoded_body)``.

        ``404`` and ``422`` come back as values rather than exceptions: both are
        ordinary, documented outcomes of a write and the caller has to decide
        what they mean. Anything else non-2xx raises.
        """
        url = f"{self.cfg.vendor_base_url}{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        data = json.dumps(body).encode("utf-8") if body is not None else None

        reauthed = False
        for attempt in range(_MAX_ATTEMPTS):
            headers = {"Authorization": f"Bearer {self._bearer()}"}
            if data is not None:
                headers["Content-Type"] = "application/json"
            if idempotency_key:
                headers["Idempotency-Key"] = idempotency_key
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return resp.status, json.load(resp)
            except urllib.error.HTTPError as exc:
                if exc.code in (404, 422):
                    try:
                        return exc.code, json.load(exc)
                    except Exception:  # noqa: BLE001
                        return exc.code, {}
                if exc.code == 401 and not reauthed:
                    reauthed = True
                    self._token = None
                    continue
                if exc.code == 429 or exc.code >= 500:
                    self._sleep_for(exc, attempt)
                    continue
                raise PlacemintError(f"{method} {path} -> {exc.code}") from exc
            except urllib.error.URLError:
                time.sleep(_BACKOFF_S * (2 ** attempt))
        raise PlacemintError(f"{method} {path}: gave up after {_MAX_ATTEMPTS} attempts")

    # -- the calls this task needs -----------------------------------------
    def get_placement(self, placement_id: str) -> tuple[int, Any]:
        """``(status, record)``; status is 404 if the id was never issued."""
        return self._request("GET", f"/api/placements/{placement_id}")

    def update_placement(self, placement_id: str, fields: dict[str, Any], *,
                         idempotency_key: str) -> tuple[int, Any]:
        """PATCH one placement. ``(status, body)``; 404 unknown, 422 rejected."""
        status, body = self._request(
            "PATCH", f"/api/placements/{placement_id}", body=fields,
            idempotency_key=idempotency_key,
        )
        if status == 200:
            self.placements_updated += 1
        return status, body

    def create_note(self, placement_id: str, note_body: str, author: str, *,
                    idempotency_key: str) -> tuple[int, Any]:
        """POST one note against a placement. ``(status, body)``."""
        status, body = self._request(
            "POST", f"/api/placements/{placement_id}/notes",
            body={"body": note_body, "author": author},
            idempotency_key=idempotency_key,
        )
        if status == 201:
            self.notes_created += 1
        return status, body

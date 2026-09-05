"""OAuth client-credentials auth for the Vettly connector (provided). See ``PROBLEM.md``."""

from __future__ import annotations

import dataclasses
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


@dataclasses.dataclass
class TokenState:
    access_token: str
    refresh_token: str


class VettlyAuth:
    """Owns the current OAuth grant for one Vettly tenant connection."""

    def __init__(self, base_url: str, client_id: str, client_secret: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self._state: TokenState | None = None

    # ------------------------------------------------------------------ HTTP
    def _post_token(self, form: dict[str, str]) -> tuple[int, dict[str, Any]]:
        """POST /oauth/token."""
        body = urllib.parse.urlencode(form).encode("utf-8")
        while True:
            req = urllib.request.Request(
                f"{self.base_url}/oauth/token",
                data=body,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return resp.getcode(), json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                payload = json.loads(exc.read().decode("utf-8") or "{}")
                if exc.code == 429:
                    retry_after = int(exc.headers.get("Retry-After", "20"))
                    time.sleep(retry_after)
                    continue
                return exc.code, payload

    # -------------------------------------------------------------- grants
    def _client_credentials_grant(self) -> None:
        status, body = self._post_token({
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        })
        if status != 200:
            raise RuntimeError(f"client_credentials grant failed: {status} {body}")
        self._state = TokenState(
            access_token=body["access_token"],
            refresh_token=body["refresh_token"],
        )

    def _refresh(self) -> bool:
        """Attempt to rotate the current refresh token. Returns True on
        success (state updated with the new access+refresh tokens), False on
        any failure (chain is dead; caller must fall back to a fresh grant)."""
        if self._state is None:
            return False
        status, body = self._post_token({
            "grant_type": "refresh_token",
            "refresh_token": self._state.refresh_token,
        })
        if status != 200:
            return False
        self._state = TokenState(
            access_token=body["access_token"],
            refresh_token=body["refresh_token"],
        )
        return True

    # ------------------------------------------------------------ public API
    def bearer_header(self) -> dict[str, str]:
        """Return the ``Authorization`` header for the current access token,
        minting a grant first if we don't have one yet."""
        if self._state is None:
            self._client_credentials_grant()
        return {"Authorization": f"Bearer {self._state.access_token}"}

    def handle_401(self) -> None:
        """Re-authenticate after a 401 on ``/v1/*`` so the next request can retry."""
        if self._state is not None and self._refresh():
            return
        self._client_credentials_grant()

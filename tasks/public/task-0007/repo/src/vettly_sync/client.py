"""HTTP client for the Vettly ``/v1`` data plane (provided). See ``PROBLEM.md``."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from .auth import VettlyAuth


class CursorExpiredError(Exception):
    """Raised when a list endpoint reports an expired cursor."""


class VettlyClient:
    def __init__(self, base_url: str, auth: VettlyAuth) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth = auth

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{query}"

        for attempt in range(2):
            headers = self.auth.bearer_header()
            req = urllib.request.Request(url, method="GET", headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and attempt == 0:
                    self.auth.handle_401()
                    continue
                if exc.code == 410:
                    raise CursorExpiredError() from exc
                raise

        raise RuntimeError(f"exhausted retries for GET {path}")

    def list_page(
        self,
        plural: str,
        cursor: str | None = None,
        modified_since: Any | None = None,
    ) -> dict[str, Any]:
        """Fetch one page of ``/v1/<plural>``.

        Raises ``CursorExpiredError`` when the cursor is no longer valid.
        """
        params: dict[str, Any] = {"cursor": cursor}
        if modified_since is not None:
            params["modified_since"] = str(modified_since)
        return self._get(f"/v1/{plural}", params)

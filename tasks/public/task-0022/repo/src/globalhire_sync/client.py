"""GlobalHire HTTP client. See PROBLEM.md and docs/."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator

from globalhire_sync.config import Config

_TIMEOUT_S = 30.0
_PAGE_LIMIT = 100  # server default & max; requesting more is clamped to 100.


class GlobalHireError(RuntimeError):
    pass


class GlobalHireClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._base = cfg.vendor_base_url.rstrip("/")

    # -- low-level HTTP -------------------------------------------------

    def _get(self, path: str, query: dict[str, str] | None = None) -> dict[str, Any]:
        url = f"{self._base}{path}"
        if query:
            url += "?" + urllib.parse.urlencode(query)
        req = urllib.request.Request(
            url, method="GET", headers={"X-GH-Key": self.cfg.api_key}
        )
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                body = resp.read()
        except urllib.error.HTTPError as exc:
            raise GlobalHireError(
                f"GET {path} failed: {exc.code} {exc.read()[:200]!r}"
            ) from exc
        return json.loads(body)

    # -- data plane -------------------------------------------------------

    def iter_candidates(self, modified_since: str | None = None) -> Iterator[dict[str, Any]]:
        """Yield candidates from the list endpoint, paging to exhaustion."""
        offset = 0
        while True:
            query: dict[str, str] = {"offset": str(offset), "limit": str(_PAGE_LIMIT)}
            if modified_since:
                query["modified_since"] = modified_since
            page = self._get("/v1/candidates", query)
            data = page.get("data", [])
            for rec in data:
                yield rec
            if len(data) < _PAGE_LIMIT:
                break
            offset += _PAGE_LIMIT

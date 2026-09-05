"""GlobalHire HTTP transport (provided). See ``PROBLEM.md``."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterator

from globalhire_sync.config import Config

_TIMEOUT_S = 60.0
DEFAULT_LIMIT = 100  # server default & max; requesting more is clamped to 100.


@dataclass
class GHResponse:
    status: int
    headers: dict[str, str]
    body: dict[str, Any]


class GlobalHireClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._base = cfg.vendor_base_url.rstrip("/")

    def get(self, path: str, params: dict[str, Any] | None = None) -> GHResponse:
        """One GET against ``{base}{path}``.

        Returns status, lower-cased response headers, and parsed JSON body.
        ``None``-valued params are omitted from the query string.
        """
        url = f"{self._base}{path}"
        query = {k: v for k, v in (params or {}).items() if v is not None}
        if query:
            url += "?" + urllib.parse.urlencode(query)
        req = urllib.request.Request(url, method="GET", headers={"X-GH-Key": self.cfg.api_key})
        try:
            with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                raw = resp.read()
                status = resp.status
                headers = {k.lower(): v for k, v in resp.headers.items()}
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            status = exc.code
            headers = {k.lower(): v for k, v in (exc.headers.items() if exc.headers else [])}
        body = json.loads(raw) if raw else {}
        return GHResponse(status=status, headers=headers, body=body if isinstance(body, dict) else {})

    def iter_offset_pages(
        self, path: str, modified_since: str | None = None, limit: int = DEFAULT_LIMIT
    ) -> Iterator[GHResponse]:
        """Page ``path`` (an offset/limit list endpoint) to exhaustion."""
        offset = 0
        while True:
            resp = self.get(path, {"offset": offset, "limit": limit, "modified_since": modified_since})
            yield resp
            data = resp.body.get("data", [])
            if not isinstance(data, list) or len(data) < limit:
                break
            offset += limit

    def iter_cursor_pages(
        self, path: str, modified_since: str | None = None, limit: int = DEFAULT_LIMIT
    ) -> Iterator[GHResponse]:
        """Page ``path`` (a cursor list endpoint) to exhaustion."""
        cursor: str | None = None
        while True:
            resp = self.get(path, {"cursor": cursor, "limit": limit, "modified_since": modified_since})
            yield resp
            cursor = resp.body.get("cursor")
            if not cursor:
                break

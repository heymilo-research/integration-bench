"""CrewCall HTTP client. See ``PROBLEM.md``."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from crewcall_sync.config import Config

_TIMEOUT_S = 60.0


class CrewCallClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._base = cfg.vendor_base_url.rstrip("/")

    # -- low-level HTTP -----------------------------------------------------

    def _get_page(self, collection: str, offset: int, limit: int) -> list[dict[str, Any]]:
        """Fetch one page. Raises ``urllib.error.HTTPError`` on non-2xx."""
        url = f"{self._base}/v1/{collection}?" + urllib.parse.urlencode(
            {"offset": str(offset), "limit": str(limit)}
        )
        req = urllib.request.Request(
            url, method="GET", headers={"Authorization": f"Bearer {self.cfg.api_key}"}
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            body = resp.read()
        return json.loads(body).get("data", [])

    # -- one full pass, offset 0 to exhaustion ---------------

    def crawl_one_pass(self, collection: str, limit: int) -> list[dict[str, Any]]:
        """Page ``collection`` from offset 0 to exhaustion; return all records in this pass."""
        records: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self._get_page(collection, offset, limit)
            records.extend(page)
            if len(page) < limit:
                return records
            offset += limit

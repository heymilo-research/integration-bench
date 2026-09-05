"""CrewCall HTTP transport. See the vendor documentation in ``docs/``."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from fenmarsh_census.config import Config

_RETRY_STATUS = {429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 6
_BACKOFF_S = 0.4
_MAX_BACKOFF_S = 10.0


class CrewCallError(RuntimeError):
    pass


class CrewCallClient:
    """Thin transport. Counts what it did so the caller can report on it."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.pages_read = 0

    def _request(self, method: str, path: str, *,
                 params: dict[str, Any] | None = None) -> Any:
        url = f"{self.cfg.vendor_base_url}/v1{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        headers = {"Authorization": f"Bearer {self.cfg.api_key}"}

        last: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            req = urllib.request.Request(url, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code not in _RETRY_STATUS:
                    detail: Any = {}
                    try:
                        detail = json.load(exc)
                    except Exception:  # noqa: BLE001
                        pass
                    raise CrewCallError(f"{method} {path} -> {exc.code}: {detail}") from exc
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                delay = (
                    min(float(retry_after), _MAX_BACKOFF_S)
                    if retry_after
                    else min(_BACKOFF_S * (2 ** attempt), _MAX_BACKOFF_S)
                )
                time.sleep(delay)
            except urllib.error.URLError as exc:
                last = exc
                time.sleep(_BACKOFF_S)
        raise CrewCallError(f"{method} {path} gave up after {_MAX_ATTEMPTS} attempts: {last}")

    def worker_page(self, *, offset: int, limit: int | None = None) -> dict[str, Any]:
        """One window of the worker roster, envelope verbatim."""
        envelope = self._request(
            "GET", "/workers",
            params={"offset": offset, "limit": limit or self.cfg.page_limit},
        )
        self.pages_read += 1
        return envelope

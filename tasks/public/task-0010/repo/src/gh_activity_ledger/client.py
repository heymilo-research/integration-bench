"""GlobalHire HTTP transport.

GlobalHire is a static-key, polling-only platform. The data plane lives under
``/v1`` and the three list routes this job reads are::

    GET /v1/candidates?offset=<n>&limit=<n>[&...]
    GET /v1/placements?offset=<n>&limit=<n>[&...]
    GET /v1/agencies?offset=<n>&limit=<n>[&...]

The key goes in the ``X-GH-Key`` header on every data-plane request; ``GET /``
is the only unauthenticated route.

``crawl()`` walks a collection from offset 0, advancing by the limit it asked
for, and returns once a page comes back holding fewer rows than it asked for.
Any extra keyword arguments are passed through as query parameters on EVERY
page of that walk, unchanged -- this layer does not know or care which
parameters the ledger wants. Records are handed back exactly as the wire sent
them; nothing here renames, reshapes or reinterprets a field.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from gh_activity_ledger.config import Config

_RETRY_STATUS = {429, 500, 502, 503, 504}

# A throttle is the vendor telling us when to come back, not an error, so the
# interval comes from Retry-After when the vendor sends one.
_MAX_ATTEMPTS = 6
_RETRY_SLEEP_S = 0.4
_MAX_RETRY_SLEEP_S = 10.0

# Nothing on this tenant is anywhere near this big; the guard is here so a
# pagination bug shows up as a failed run instead of an all-night loop.
_MAX_PAGES = 500


class GlobalHireError(RuntimeError):
    pass


class GaveUp(GlobalHireError):
    """Raised when a request could not be completed within the retry allowance."""


class GlobalHireClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.pages_fetched = 0
        self.retries_seen = 0

    # -- HTTP ------------------------------------------------------------------

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """The decoded body of a data-plane GET."""
        url = f"{self.cfg.vendor_base_url}{path}"
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        if clean:
            url = f"{url}?{urllib.parse.urlencode(clean)}"
        last: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            req = urllib.request.Request(url, headers={"X-GH-Key": self.cfg.api_key})
            try:
                with urllib.request.urlopen(req, timeout=60) as resp:
                    return json.load(resp)
            except urllib.error.HTTPError as exc:
                last = exc
                if exc.code not in _RETRY_STATUS:
                    raise
                self.retries_seen += 1
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                if retry_after:
                    delay = min(float(retry_after), _MAX_RETRY_SLEEP_S)
                else:
                    delay = min(_RETRY_SLEEP_S * (2 ** attempt), _MAX_RETRY_SLEEP_S)
                time.sleep(delay)
            except urllib.error.URLError as exc:
                last = exc
                time.sleep(_RETRY_SLEEP_S)
        raise GaveUp(f"GET {path} gave up after {_MAX_ATTEMPTS} attempts: {last}")

    # -- collections -----------------------------------------------------------

    def page(self, collection: str, *, offset: int, **params: Any) -> dict[str, Any]:
        """One page envelope of a collection, verbatim."""
        query: dict[str, Any] = {"offset": offset, "limit": 100}
        query.update(params)
        envelope = self.get(f"/v1/{collection}", query)
        self.pages_fetched += 1
        return envelope if isinstance(envelope, dict) else {}

    def crawl(self, collection: str, **params: Any) -> list[dict[str, Any]]:
        """Every row a collection returns for ``params``, walked to exhaustion."""
        rows: list[dict[str, Any]] = []
        offset = 0
        for _ in range(_MAX_PAGES):
            envelope = self.page(collection, offset=offset, **params)
            batch = list(envelope.get("data") or [])
            rows.extend(batch)
            if len(batch) < 100:
                return rows
            offset += 100
        raise GaveUp(f"/v1/{collection} did not terminate within {_MAX_PAGES} pages")

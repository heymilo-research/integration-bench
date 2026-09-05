"""SourceWell HTTP transport.

SourceWell publishes one page of documentation and a self-describing API:
``docs/index.md`` gives the auth recipe and the root URL, and every response
carries a ``_links`` block. This client authenticates with the tenant key in
the header that page names, reads the root, takes each collection's href
out of the root's ``_links``, and follows ``_links.next.href`` until it is
null, exactly as ``docs/index.md`` describes.

Records are returned EXACTLY as the wire sends them: this layer does not
rename, reinterpret, drop or filter any field, and it does not look at any
record's contents. Full vendor documentation is in ``docs/``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterator

from skill_index.config import Config

_MAX_ATTEMPTS = 6


class SourceWellError(RuntimeError):
    """Any unrecoverable transport failure."""


class SourceWellHTTPError(SourceWellError):
    def __init__(self, status: int, body: str, url: str) -> None:
        super().__init__(f"HTTP {status} for {url}: {body[:200]}")
        self.status = status
        self.body = body
        self.url = url


class SourceWellClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.request_count = 0
        self._root: dict[str, Any] | None = None

    # -- one request ---------------------------------------------------------

    def _url(self, href: str) -> str:
        """Absolutise an href against the sandbox base URL."""
        if href.startswith("http://") or href.startswith("https://"):
            return href
        return urllib.parse.urljoin(self.cfg.vendor_base_url + "/", href.lstrip("/"))

    def get(self, href: str) -> dict[str, Any]:
        url = self._url(href)
        last: Exception | None = None
        for attempt in range(_MAX_ATTEMPTS):
            req = urllib.request.Request(
                url,
                headers={"X-SW-Key": self.cfg.api_key, "Accept": "application/json"},
                method="GET",
            )
            self.request_count += 1
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                body = exc.read().decode("utf-8", "replace")
                last = exc
                if exc.code == 429:
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    wait = 2.0
                    try:
                        wait = max(float(retry_after), 0.0)
                    except (TypeError, ValueError):
                        try:
                            wait = max(float(json.loads(body).get("retry_after_s")), 0.0)
                        except (ValueError, TypeError, AttributeError):
                            pass
                    time.sleep(min(wait, 30.0))
                    continue
                if 500 <= exc.code < 600:
                    time.sleep(min(2.0 * (attempt + 1), 10.0))
                    continue
                raise SourceWellHTTPError(exc.code, body, url) from exc
            except urllib.error.URLError as exc:
                last = exc
                time.sleep(1.0)
        raise SourceWellError(f"GET {url} gave up after {_MAX_ATTEMPTS} attempts: {last}")

    # -- the crawl -----------------------------------------------------------

    def root(self) -> dict[str, Any]:
        """The crawl entry point, read once per pass and kept: SourceWell is a
        read-only database and its ``_links`` block does not move under us."""
        if self._root is None:
            self._root = self.get("/x/")
        return self._root

    def collection_href(self, rel: str) -> str:
        """The href the root's ``_links`` block gives for a collection."""
        links = self.root().get("_links") or {}
        target = links.get(rel)
        if not isinstance(target, dict) or not target.get("href"):
            raise SourceWellError(f"the root document has no {rel!r} link: {links!r}")
        return str(target["href"])

    def profile_pages(self) -> Iterator[list[dict[str, Any]]]:
        """Yield each page's ``data`` array for the profiles collection."""
        return self.collection_pages("profiles")

    def crawl_profiles(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for page in self.profile_pages():
            rows.extend(page)
        return rows

    def collection_pages(self, rel: str) -> Iterator[list[dict[str, Any]]]:
        """Yield each page's ``data`` array for any collection the root links."""
        href: str | None = self.collection_href(rel)
        while href:
            doc = self.get(href)
            yield list(doc.get("data") or [])
            links = doc.get("_links") or {}
            nxt = links.get("next") if isinstance(links, dict) else None
            href = str(nxt["href"]) if isinstance(nxt, dict) and nxt.get("href") else None

    def crawl_tags(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for page in self.collection_pages("tags"):
            rows.extend(page)
        return rows

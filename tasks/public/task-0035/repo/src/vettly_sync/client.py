"""HTTP client for the Vettly `/v1` data plane. See ``PROBLEM.md``."""

from __future__ import annotations

from typing import Any, Iterator

from .auth import VettlyAuth


class VettlyClient:
    def __init__(self, base_url: str, auth: VettlyAuth) -> None:
        self.base_url = base_url.rstrip("/")
        self.auth = auth

    def iter_collection(
        self,
        plural: str,
        modified_since: Any | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield raw records from ``/v1/<plural>``, with optional incremental filtering."""
        raise NotImplementedError

"""GlobalHire HTTP client. See ``PROBLEM.md``."""

from __future__ import annotations

from typing import Any, Iterator

from globalhire_sync.config import Config


class GlobalHireClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def iter_candidates(self, modified_since: str | None = None) -> Iterator[dict[str, Any]]:
        """Yield candidates from the vendor API, paging until exhausted.

        Args:
            modified_since: optional filter for incremental sync (see docs).
        """
        raise NotImplementedError

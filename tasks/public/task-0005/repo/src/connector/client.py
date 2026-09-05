"""TalentForge HTTP client + auth. See ``PROBLEM.md``."""

from __future__ import annotations

from typing import Any, Iterator

from connector.config import Config


class TalentForgeClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def authenticate(self) -> None:
        """Establish a data-plane session."""
        raise NotImplementedError

    def iter_candidates(self, modified_since: str | None = None) -> Iterator[dict[str, Any]]:
        """Yield candidates, optionally filtered by modification time."""
        raise NotImplementedError

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        """Fetch a single candidate by id (or None if it does not exist)."""
        raise NotImplementedError

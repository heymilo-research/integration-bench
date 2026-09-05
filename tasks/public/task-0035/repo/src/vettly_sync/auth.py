"""OAuth token management for the Vettly connector. See ``PROBLEM.md``."""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass
class TokenState:
    access_token: str
    refresh_token: str


class VettlyAuth:
    """Owns the current OAuth grant for one Vettly tenant connection."""

    def __init__(self, base_url: str, client_id: str, client_secret: str) -> None:
        self.base_url = base_url
        self.client_id = client_id
        self.client_secret = client_secret
        self._state: TokenState | None = None

    def bearer_header(self) -> dict[str, str]:
        """Return the ``Authorization`` header, ensuring a valid access token."""
        raise NotImplementedError

    def handle_401(self) -> None:
        """Recover from an unauthorized response so the caller can retry."""
        raise NotImplementedError

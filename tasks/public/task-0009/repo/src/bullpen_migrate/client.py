"""Bullpen v2 HTTP client (auth routing). See ``PROBLEM.md``."""

from __future__ import annotations

from bullpen_migrate import config
from bullpen_migrate.legacy_client import LegacyClient


class BullpenClient:
    def __init__(self, auth_mode: str = "legacy") -> None:
        self.auth_mode = auth_mode
        self._legacy = LegacyClient(config.VENDOR_BASE_URL, config.BP_APP_TOKEN)

    def get(self, path: str) -> dict:
        return self._legacy.get(path)

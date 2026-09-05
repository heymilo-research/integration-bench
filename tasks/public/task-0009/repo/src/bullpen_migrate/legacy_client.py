"""Legacy app-token HTTP client (provided). See ``PROBLEM.md``."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request


class BullpenHTTPError(RuntimeError):
    def __init__(self, status: int, body: dict) -> None:
        super().__init__(f"bullpen error {status}: {body}")
        self.status = status
        self.body = body


class LegacySunsetError(BullpenHTTPError):
    """Legacy app-token auth rejected."""


class LegacyClient:
    MAX_429_RETRIES = 20

    def __init__(self, base_url: str, app_token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.app_token = app_token

    @staticmethod
    def _read_body(err: urllib.error.HTTPError) -> dict:
        try:
            return json.loads(err.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def get(self, path: str) -> dict:
        attempts_429 = 0
        while True:
            req = urllib.request.Request(
                f"{self.base_url}{path}",
                method="GET",
                headers={"X-BP-App-Token": self.app_token},
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as err:
                body = self._read_body(err)
                if err.code == 410:
                    raise LegacySunsetError(410, body) from err
                if err.code == 429:
                    attempts_429 += 1
                    if attempts_429 > self.MAX_429_RETRIES:
                        raise BullpenHTTPError(429, body) from err
                    time.sleep(float(body.get("retry_after_s", 1)))
                    continue
                raise BullpenHTTPError(err.code, body) from err

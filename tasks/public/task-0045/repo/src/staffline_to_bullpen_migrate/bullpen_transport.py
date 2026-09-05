"""OAuth client-credentials HTTP transport for Bullpen v2."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request


class BullpenHTTPError(RuntimeError):
    def __init__(self, status: int, body: dict) -> None:
        super().__init__(f"bullpen error {status}: {body}")
        self.status = status
        self.body = body


class BullpenTransport:
    MAX_429_RETRIES = 20
    MAX_403_RETRIES = 1

    def __init__(self, base_url: str, client_id: str, client_secret: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: str | None = None

    @staticmethod
    def _read_body(err: urllib.error.HTTPError) -> dict:
        try:
            return json.loads(err.read().decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    def _mint(self) -> None:
        body = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }
        ).encode()
        req = urllib.request.Request(
            f"{self.base_url}/oauth/token",
            method="POST",
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        self._token = payload["access_token"]

    def _request(self, method: str, path: str, data: bytes | None, extra_headers: dict[str, str]) -> dict:
        if self._token is None:
            self._mint()
        attempts_429 = 0
        attempts_403 = 0
        while True:
            headers = {"Authorization": f"Bearer {self._token}", **extra_headers}
            req = urllib.request.Request(f"{self.base_url}{path}", method=method, data=data, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as err:
                body = self._read_body(err)
                if err.code == 429:
                    attempts_429 += 1
                    if attempts_429 > self.MAX_429_RETRIES:
                        raise BullpenHTTPError(429, body) from err
                    time.sleep(float(body.get("retry_after_s", 1)))
                    continue
                if err.code == 403:
                    attempts_403 += 1
                    if attempts_403 > self.MAX_403_RETRIES:
                        raise BullpenHTTPError(403, body) from err
                    self._mint()
                    continue
                if err.code == 422:
                    return {"_status": 422, "_body": body}
                raise BullpenHTTPError(err.code, body) from err

    def get(self, path: str) -> dict:
        return self._request("GET", path, None, {})

    def post(self, path: str, payload: dict, idempotency_key: str | None = None) -> dict:
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return self._request("POST", path, data, headers)

    def patch(self, path: str, payload: dict, idempotency_key: str | None = None) -> dict:
        data = json.dumps(payload).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return self._request("PATCH", path, data, headers)

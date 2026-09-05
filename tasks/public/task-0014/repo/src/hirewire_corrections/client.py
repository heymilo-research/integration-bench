"""HTTP transport for HireWire's v1 API (provided). See ``PROBLEM.md``."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from hirewire_corrections.config import Config

_TIMEOUT_S = 30.0
_MAX_RATE_LIMIT_WAITS = 5


class HireWireClient:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._base = cfg.vendor_base_url.rstrip("/")

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {"Authorization": f"Bearer {self.cfg.api_key}"}
        if extra:
            headers.update(extra)
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        url = f"{self._base}{path}"
        if params:
            clean = {k: v for k, v in params.items() if v is not None}
            if clean:
                url = f"{url}?{urllib.parse.urlencode(clean)}"

        data: bytes | None = None
        hdrs = self._headers(headers)
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"

        waits = 0
        while True:
            req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
            try:
                with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
                    raw = resp.read()
                    return resp.getcode(), (json.loads(raw) if raw else {})
            except urllib.error.HTTPError as exc:
                raw = exc.read()
                try:
                    parsed = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    parsed = {"error": raw.decode("utf-8", "replace")}
                if exc.code == 429 and waits < _MAX_RATE_LIMIT_WAITS:
                    retry_after_hdr = exc.headers.get("Retry-After") if exc.headers else None
                    retry_after = int(retry_after_hdr) if retry_after_hdr else 1
                    time.sleep(retry_after)
                    waits += 1
                    continue
                return exc.code, parsed

    # -- reads ----------------------------------------------------------

    def list_candidates(
        self, page: int = 1, per_page: int = 100, modified_since: int | None = None
    ) -> tuple[int, dict[str, Any]]:
        return self._request(
            "GET",
            "/v1/candidates",
            params={"page": page, "per_page": per_page, "modified_since": modified_since},
        )

    def get_candidate(self, candidate_id: str) -> tuple[int, dict[str, Any]]:
        return self._request("GET", f"/v1/candidates/{candidate_id}")

    # -- writes -----------------------------------------------------------

    def patch_candidate(
        self, candidate_id: str, fields: dict[str, Any], idempotency_key: str | None = None
    ) -> tuple[int, dict[str, Any]]:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        return self._request("PATCH", f"/v1/candidates/{candidate_id}", body=fields, headers=headers)

    def create_event(
        self,
        candidate_id: str,
        event_type: str,
        note: str = "",
        idempotency_key: str | None = None,
    ) -> tuple[int, dict[str, Any]]:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        body = {"event_type": event_type, "note": note}
        return self._request(
            "POST", f"/v1/candidates/{candidate_id}/events", body=body, headers=headers
        )

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class Response:
    status: int
    body: dict[str, Any]
    headers: dict[str, str]


class GlobalHireError(RuntimeError):
    def __init__(self, status: int, body: dict[str, Any], operation: str):
        super().__init__(f"GlobalHire {operation} failed with HTTP {status}: {body}")
        self.status = status
        self.body = body
        self.operation = operation


class GlobalHireClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Response:
        request_headers = {"X-GH-Key": self.api_key, **(headers or {})}
        data = None
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = Request(
            self.base_url + path,
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=20) as response:
                raw = response.read()
                body = json.loads(raw) if raw else {}
                return Response(
                    response.status,
                    body if isinstance(body, dict) else {},
                    {key.lower(): value for key, value in response.headers.items()},
                )
        except HTTPError as exc:
            try:
                raw_body = json.loads(exc.read() or b"{}")
            except (ValueError, TypeError):
                raw_body = {}
            return Response(
                exc.code,
                raw_body if isinstance(raw_body, dict) else {},
                {key.lower(): value for key, value in exc.headers.items()},
            )
        except URLError as exc:
            raise RuntimeError(f"GlobalHire transport failure: {exc}") from exc

    def _record(self, collection: str, record_id: str) -> dict[str, Any] | None:
        response = self._request(
            "GET", f"/v1/{collection}/{quote(record_id, safe='')}"
        )
        if response.status == 404:
            return None
        if response.status != 200:
            raise GlobalHireError(response.status, response.body, f"read {collection}")
        return response.body

    def candidate(self, candidate_id: str) -> dict[str, Any] | None:
        return self._record("candidates", candidate_id)

    def placement(self, placement_id: str) -> dict[str, Any] | None:
        return self._record("placements", placement_id)

    def agency(self, agency_id: str) -> dict[str, Any] | None:
        return self._record("agencies", agency_id)

    def _post_updates(self, updates: list[dict[str, str]]) -> Response:
        return self._request(
            "POST",
            "/v1/candidates/status-batch",
            payload={"updates": updates},
        )

    def update_stages(self, updates: list[dict[str, str]]) -> list[dict[str, Any]]:
        if not updates:
            return []
        response = self._post_updates(updates)
        if response.status == 413:
            capacity = int(response.body.get("max_items") or 1)
            response = self._post_updates(updates[:capacity])
        if response.status != 200:
            raise GlobalHireError(response.status, response.body, "update stages")
        rows = response.body.get("updated")
        return rows if isinstance(rows, list) else []

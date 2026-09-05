"""Minimal requests-compatible surface for the offline participant image."""

from __future__ import annotations

import json as _json
from collections.abc import Mapping
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class Headers(Mapping[str, str]):
    """Read-only, case-insensitive response headers."""

    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = {str(key).lower(): str(value) for key, value in values.items()}

    def __getitem__(self, key: str) -> str:
        return self._values[key.lower()]

    def __iter__(self):
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def get(self, key: str, default: str | None = None) -> str | None:
        return self._values.get(key.lower(), default)


class Response:
    def __init__(
        self, status_code: int, headers: Mapping[str, str], content: bytes
    ) -> None:
        self.status_code = status_code
        self.headers = Headers(headers)
        self.content = content

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return _json.loads(self.content)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            detail = self.text[:500]
            raise RuntimeError(f"HTTP {self.status_code}: {detail}")


class Session:
    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        data: bytes | str | None = None,
        json_body: Any | None = None,
        json: Any | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float = 10.0,
    ) -> Response:
        if params:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}{urlencode(params, doseq=True)}"

        payload: bytes | None
        request_headers = dict(headers or {})
        json_value = json if json is not None else json_body
        if json_value is not None:
            payload = _json.dumps(json_value).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        elif isinstance(data, str):
            payload = data.encode("utf-8")
        else:
            payload = data

        request = Request(
            url, data=payload, headers=request_headers, method=method.upper()
        )
        try:
            with urlopen(request, timeout=timeout) as raw:
                return Response(raw.status, dict(raw.headers.items()), raw.read())
        except HTTPError as error:
            return Response(error.code, dict(error.headers.items()), error.read())

    def post(self, url: str, **kwargs: Any) -> Response:
        return self.request("POST", url, **kwargs)

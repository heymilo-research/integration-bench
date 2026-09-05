"""HTTP client for the StaffLine web-services API (provided). See ``PROBLEM.md``."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from staffline_bulk.config import Config


class VendorError(RuntimeError):
    """Raised for a transport-level failure talking to StaffLine."""


class StafflineClient:
    """Thin signed-request wrapper over StaffLine's ``/svc`` endpoints."""

    def __init__(self, config: Config) -> None:
        self._base_url = config.vendor_base_url
        self._token = config.app_token
        self._secret = config.hmac_secret

    # ------------------------------------------------------------------ auth
    def _sign(self, timestamp: str, body: bytes) -> str:
        msg = timestamp.encode("utf-8") + b"." + body
        return hmac.new(self._secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()

    def _auth_headers(self, body: bytes) -> dict[str, str]:
        ts = str(int(time.time()))
        return {
            "X-SL-Token": self._token,
            "X-SL-Timestamp": ts,
            "X-SL-Signature": self._sign(ts, body),
        }

    # --------------------------------------------------------------- request
    def _request(
        self, method: str, path: str, *, query: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> tuple[int, Any]:
        url = self._base_url + path
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else b""
        headers = self._auth_headers(body)
        if payload is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body if payload is not None else None,
                                      method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return resp.status, self._parse(resp.read())
        except urllib.error.HTTPError as err:
            return err.code, self._parse(err.read())
        except urllib.error.URLError as err:
            raise VendorError(f"transport error calling {method} {path}: {err}") from err

    @staticmethod
    def _parse(raw: bytes) -> Any:
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw.decode("utf-8", errors="replace")

    # --------------------------------------------------------------- reads
    def get_candidate(self, candidate_id: str) -> tuple[int, Any]:
        """``GET /svc/candidates/{id}``. See ``docs/reference.md``."""
        return self._request("GET", f"/svc/candidates/{candidate_id}")

    def list_candidates(
        self, *, start: int = 0, count: int = 50, modified_since: int | None = None
    ) -> tuple[int, Any]:
        """``GET /svc/candidates`` — one page: ``{"rows": [...], "more": bool}``."""
        query: dict[str, Any] = {"start": start, "count": count}
        if modified_since is not None:
            query["modified_since"] = modified_since
        return self._request("GET", "/svc/candidates", query=query)

    # --------------------------------------------------------------- bulk
    def bulk_create(self, items: list[dict[str, Any]]) -> tuple[int, Any]:
        """``POST /svc/candidates/bulk``. See ``docs/reference.md``."""
        return self._request("POST", "/svc/candidates/bulk", payload={"items": items})

    # --------------------------------------------------------------- RPC writeback
    def rpc_do(self, action: str, payload: dict[str, Any]) -> tuple[int, Any]:
        """``POST /svc/do?action=<action>``. See ``docs/reference.md``."""
        return self._request("POST", "/svc/do", query={"action": action}, payload=payload)

    def create_note(self, candidate_id: str, note_text: str, *, created_by: str = "") -> dict[str, Any]:
        status, body = self.rpc_do(
            "createNote",
            {"candidate_id": candidate_id, "note_text": note_text, "created_by": created_by},
        )
        if status != 200 or not isinstance(body, dict) or not body.get("ok"):
            raise VendorError(f"createNote failed: {status} {body!r}")
        return body

    def update_candidate(self, candidate_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        status, body = self.rpc_do("updateCandidate", {"candidate_id": candidate_id, **fields})
        if status != 200 or not isinstance(body, dict) or not body.get("ok"):
            raise VendorError(f"updateCandidate failed: {status} {body!r}")
        return body

"""Onboardly provisioning bridge for placed candidates. See ``PROBLEM.md``."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from talentforge_hooks.config import Config

_DEFAULT_RETRY_AFTER_S = 5.0
_MAX_ATTEMPTS = 4


class OnboardlyBridge:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.base = cfg.onboardly_base_url
        # candidate_id -> report entry; rewritten to disk on every change so a
        # crash between events never loses recorded outcomes.
        self._report: dict[str, dict[str, Any]] = {}

    # -- low-level HTTP (provided) -------------------------------------------

    def _raw(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, bytes]:
        url = f"{self.base}{path}"
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("X-OB-Key", self.cfg.ob_api_key)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read()

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        """JSON request honoring ``429 Retry-After``."""
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        hdrs = dict(headers or {})
        if body is not None:
            hdrs["Content-Type"] = "application/json"
        for _ in range(_MAX_ATTEMPTS):
            status, raw = self._raw(method, path, body=body, headers=hdrs)
            if status == 429:
                time.sleep(_DEFAULT_RETRY_AFTER_S)
                continue
            return status, _parse_json(raw)
        raise RuntimeError(f"{method} {path}: gave up after {_MAX_ATTEMPTS} attempts")

    # -- the bridge --------------------------------------------

    def evaluate(self, candidate: dict[str, Any]) -> None:
        """Called once per applied candidate event with the fetched upstream record.

        See PROBLEM.md for the report contract.
        """
        raise NotImplementedError

    def _provision(self, candidate: dict[str, Any]) -> dict[str, Any]:
        """Create the packet for one candidate and return the report entry.

        See ``docs/onboardly/writeback.md``.
        """
        raise NotImplementedError

    # -- report writer (provided) ---------------------------------------------

    def write_report(self) -> None:
        provisioned = sorted(
            (e for e in self._report.values() if "packet" in e),
            key=lambda e: e["candidate_id"],
        )
        skipped = sorted(
            (e for e in self._report.values() if "packet" not in e),
            key=lambda e: e["candidate_id"],
        )
        out = {"provisioned": provisioned, "skipped": skipped}
        self.cfg.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.cfg.output_dir / "bridge_result.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(out, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)


def _parse_json(body: bytes) -> Any:
    if not body:
        return {}
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}

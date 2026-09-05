"""Typed async client for the vendorsim control plane (docs/vendorsim-config.md §11).

All endpoints are JSON over HTTP on the control-plane port (default 9000),
reachable only from control-net (i.e. only during `bench grade`, never
`bench run`). This client is a thin wrapper — it does not interpret responses
beyond JSON-decoding them; verifier scenarios and the builtin L2 checks own
interpretation.
"""

from __future__ import annotations

from typing import Any

import httpx


class ControlClient:
    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> "ControlClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # -- §11 endpoints --------------------------------------------------------

    async def reset(self, seed: int | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if seed is not None:
            payload["seed"] = seed
        r = await self._client.post("/control/reset", json=payload)
        r.raise_for_status()
        return r.json()

    async def mutations_start(self) -> dict[str, Any]:
        r = await self._client.post("/control/mutations/start", json={})
        r.raise_for_status()
        return r.json()

    async def mutations_status(self) -> dict[str, Any]:
        r = await self._client.get("/control/mutations/status")
        r.raise_for_status()
        return r.json()

    async def set_faults(self, **flags: Any) -> dict[str, Any]:
        r = await self._client.post("/control/faults", json=flags)
        r.raise_for_status()
        return r.json()

    async def state(self, entity: str) -> Any:
        r = await self._client.get(f"/control/state/{entity}")
        r.raise_for_status()
        return r.json()

    async def request_log(self) -> list[dict[str, Any]]:
        r = await self._client.get("/control/request-log")
        r.raise_for_status()
        return _unwrap(r.json(), "requests")

    async def webhook_deliveries(self) -> list[dict[str, Any]]:
        r = await self._client.get("/control/webhook-deliveries")
        r.raise_for_status()
        return _unwrap(r.json(), "deliveries")

    async def tokens(self) -> list[dict[str, Any]]:
        r = await self._client.get("/control/tokens")
        r.raise_for_status()
        return _unwrap(r.json(), "tokens")


def _unwrap(payload: Any, key: str) -> list[dict[str, Any]]:
    """The vendorsim control plane wraps its log endpoints in a named envelope
    (docs/vendorsim-config.md §11 / controlplane.py: ``{"requests": [...]}``,
    ``{"deliveries": [...]}``, ``{"tokens": [...]}``). The scenario SDK and
    builtin_l2 consume these as bare lists, so unwrap the documented envelope key
    here (tolerant of an already-bare list, for hand-written test doubles)."""
    if isinstance(payload, dict):
        return payload.get(key, [])
    return payload

"""Generate / cross-check task-0021's answer-key fixtures.

Boots the REAL globalhire FastAPI app in-process (uvicorn, on an ephemeral
localhost port) with exactly this task's env (VENDOR_SEED=5000, CHECKPOINT=0,
GH_V2_ENABLED=1, GH_V1_TRUNCATE=candidates:100, GH_V2_RPC_COLLECTION=placements
— see docker-compose.yaml), then crawls it over real HTTP with the SAME
per-collection discovery algorithm the gold solution uses (see
solution.patch's sync.py): probe `/v1/<collection>`'s first page; if it
carries the `Deprecation`/`Link` breadcrumb, crawl the full set via
`/v2/<collection>` (cursor pagination); otherwise crawl `/v1/<collection>` to
exhaustion (offset pagination). This produces exactly the output a correct
connector run against this task's sandbox should produce.

A second, independent derivation cross-checks row COUNTS (not full field
equality, since transport-level canonicalization is the crawl's job) directly
against `globalhire.state.build_state(seed=5000, checkpoint=0)` — the vendor's
own pure-Python ground truth — so a bug in this script's HTTP crawl can't
silently poison the fixtures without at least a count mismatch surfacing.

Usage (no Docker required — this only needs the vendor's own `src/` on
sys.path and a free localhost port):

    python3 verifier/fixtures/generate_fixtures.py

Writes:
    candidates_backfill.json  (expect 6000 rows, sourced via /v2)
    placements_backfill.json  (expect 400 rows, sourced via /v1)
    agencies_backfill.json    (expect 15 rows, sourced via /v1 -- either
                               version is byte-identical for this collection)
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FIXTURES_DIR = Path(__file__).resolve().parent
TASK_DIR = FIXTURES_DIR.parents[1]

# Resolve the canonical monorepo vendor source.
_DEFAULT_VENDOR_SRC = (
    TASK_DIR.parents[2] / "vendors" / "globalhire" / "src"
)
VENDOR_SRC = Path(os.environ.get("GLOBALHIRE_VENDOR_SRC", str(_DEFAULT_VENDOR_SRC)))

# This task's env (must match docker-compose.yaml exactly).
SEED = 5000
CHECKPOINT = 0
API_KEY = "gh-test-api-key"
TASK_ENV = {
    "VENDOR_SEED": str(SEED),
    "CHECKPOINT": str(CHECKPOINT),
    "GH_API_KEY": API_KEY,
    "GH_V2_ENABLED": "1",
    "GH_V1_TRUNCATE": "candidates:100",
    "GH_V2_RPC_COLLECTION": "placements",
    "REQUEST_LOG_PATH": "/tmp/task-0021-fixture-gen/requests.jsonl",
    "TOKEN_LOG_PATH": "/tmp/task-0021-fixture-gen/tokens.jsonl",
}

_COLLECTIONS = ("candidates", "placements", "agencies")
_EXPECTED_COUNTS = {"candidates": 6000, "placements": 400, "agencies": 15}
_EXPECTED_SOURCE = {"candidates": "v2", "placements": "v1", "agencies": "v1"}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _boot_vendor() -> tuple[Any, int]:
    """Import the vendor app with this task's env already set, then serve it
    on an ephemeral port in a background thread. Returns (server, port)."""
    sys.path.insert(0, str(VENDOR_SRC))
    os.environ.update(TASK_ENV)

    import uvicorn  # noqa: PLC0415 (import after sys.path/env setup, deliberately)

    from globalhire import main as gh_main  # noqa: PLC0415

    port = _free_port()
    config = uvicorn.Config(gh_main.app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1.0)
            return server, port
        except OSError:
            time.sleep(0.1)
    raise RuntimeError("vendor did not come up in time")


# ---------------------------------------------------------------------------
# HTTP crawl (mirrors solution.patch's sync.py discovery algorithm exactly).
# ---------------------------------------------------------------------------

def _get(base_url: str, path: str, params: dict[str, Any]) -> tuple[int, dict[str, str], dict[str, Any]]:
    query = {k: v for k, v in params.items() if v is not None}
    url = f"{base_url}{path}"
    if query:
        url += "?" + urllib.parse.urlencode(query)
    req = urllib.request.Request(url, headers={"X-GH-Key": API_KEY})
    with urllib.request.urlopen(req, timeout=30.0) as resp:
        headers = {k.lower(): v for k, v in resp.headers.items()}
        body = json.loads(resp.read())
        return resp.status, headers, body


def _has_successor_breadcrumb(headers: dict[str, str], collection: str) -> bool:
    if str(headers.get("deprecation", "")).lower() != "true":
        return False
    link = headers.get("link", "")
    return f"/v2/{collection}" in link and "successor-version" in link


def _crawl_v1_offset(base_url: str, collection: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    calls = 0
    while True:
        _status, _headers, body = _get(base_url, f"/v1/{collection}", {"offset": offset, "limit": 100})
        calls += 1
        data = body.get("data", [])
        rows.extend(data)
        if len(data) < 100:
            break
        offset += 100
    return rows, calls


def _crawl_v2_cursor(base_url: str, collection: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cursor = None
    calls = 0
    while True:
        _status, _headers, body = _get(base_url, f"/v2/{collection}", {"cursor": cursor, "limit": 100})
        calls += 1
        data = body.get("data", [])
        rows.extend(data)
        cursor = body.get("cursor")
        if not cursor:
            break
    return rows, calls


def _crawl_collection(base_url: str, collection: str) -> tuple[list[dict[str, Any]], str, int]:
    """Returns (raw records, source_version, total_http_calls)."""
    _status, headers, body = _get(base_url, f"/v1/{collection}", {"offset": 0, "limit": 100})
    probe_calls = 1
    if _has_successor_breadcrumb(headers, collection):
        rows, v2_calls = _crawl_v2_cursor(base_url, collection)
        return rows, "v2", probe_calls + v2_calls
    # No breadcrumb: the probe page IS page 1 of a v1 crawl -- keep paging.
    rows = list(body.get("data", []))
    offset = 100
    calls = probe_calls
    while len(body.get("data", [])) == 100:
        _status, _headers, body = _get(base_url, f"/v1/{collection}", {"offset": offset, "limit": 100})
        calls += 1
        rows.extend(body.get("data", []))
        if len(body.get("data", [])) < 100:
            break
        offset += 100
    return rows, "v1", calls


# ---------------------------------------------------------------------------
# Canonical mapping (mirrors solution.patch's sync.py canonical_record).
# ---------------------------------------------------------------------------

def _parse_offset_timestamp(value: str) -> int:
    v = value.strip()
    if v.endswith("Z"):
        v = v[:-1] + "+00:00"
    dt = datetime.fromisoformat(v)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return int(dt.astimezone(timezone.utc).timestamp())


def _canonical(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": rec["id"],
        "data": dict(rec),
        "updated_at": _parse_offset_timestamp(rec["modified_at"]),
        "is_deleted": bool(rec.get("is_deleted", False)),
    }


def _cross_check_counts() -> dict[str, int]:
    """Independent ground truth: the vendor's own state builder, no HTTP."""
    from globalhire import state  # noqa: PLC0415 (sys.path already set by caller)

    s = state.build_state(seed=SEED, checkpoint=CHECKPOINT)
    return {k: len(s[k]) for k in _COLLECTIONS}


def main() -> None:
    server, port = _boot_vendor()
    base_url = f"http://127.0.0.1:{port}"
    try:
        expected_counts = _cross_check_counts()
        print(f"state.build_state ground truth: {expected_counts}")

        for collection in _COLLECTIONS:
            raw_rows, source, calls = _crawl_collection(base_url, collection)
            rows = sorted((_canonical(r) for r in raw_rows), key=lambda r: r["source_id"])
            out_path = FIXTURES_DIR / f"{collection}_backfill.json"
            out_path.write_text(json.dumps(rows, indent=2, sort_keys=False), encoding="utf-8")

            expected_n = _EXPECTED_COUNTS[collection]
            expected_source = _EXPECTED_SOURCE[collection]
            ok_count = len(rows) == expected_n == expected_counts[collection]
            ok_source = source == expected_source
            print(
                f"{collection}: rows={len(rows)} (expected {expected_n}, "
                f"state ground truth {expected_counts[collection]}) "
                f"source={source} (expected {expected_source}) "
                f"http_calls={calls} -> {out_path.name} "
                f"[{'OK' if ok_count and ok_source else 'MISMATCH'}]"
            )
            assert ok_count, f"{collection}: row count mismatch"
            assert ok_source, f"{collection}: sourced from unexpected version"
    finally:
        server.should_exit = True
        time.sleep(0.3)


if __name__ == "__main__":
    main()

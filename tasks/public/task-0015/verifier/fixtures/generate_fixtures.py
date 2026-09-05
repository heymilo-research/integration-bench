"""Regenerate / cross-check task-0015's fixtures.

Everything in this task's fixtures is DETERMINISTIC and derivable without a
built vendor container: it boots the real ``staffline`` app in-process, via a
plain ``python3 -m uvicorn`` subprocess against the checked-out vendor
source on an EPHEMERAL port (no Docker needed), and drives it with the exact
same signed HTTP calls a real connector would make -- the same pattern
``vendors/staffline/smoke_test.py`` uses for its Docker-free bulk-feature
group.

This regenerates:
    bulk_ingest_mixed_results.json   (SL_BULK_ENABLED=1 only)
    lying_success_reconcile.json     (+ SL_LYING_REF=batch-0007, SL_RAW_LAG_REQS=3)

Usage (no Docker required):

    python3 verifier/fixtures/generate_fixtures.py

To validate end-to-end against the real running image, run the actual
connector's gold behavior against `staffline:local` per the task's own
scenarios (``bench grade`` / ``bench validate``).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# Canonical StaffLine source. Override only for isolated authoring work.
_DEFAULT_VENDOR_SRC = (
    Path(__file__).resolve().parents[5]
    / "vendors" / "staffline" / "src"
).resolve()
VENDOR_SRC = Path(os.environ.get("STAFFLINE_VENDOR_SRC", str(_DEFAULT_VENDOR_SRC)))

FIXTURES_DIR = Path(__file__).resolve().parent
REPO_ROOT = FIXTURES_DIR.parents[1]  # tasks/task-0015/
BATCH_FILE = REPO_ROOT / "repo" / "input" / "candidate_batch.json"

APP_TOKEN = "sl-test-app-token"
HMAC_SECRET = "sl-test-hmac-secret"
LYING_REF = "batch-0007"
RAW_LAG_REQS = 3
RECONCILE_MAX_ROUNDS = 8  # generous headroom over RAW_LAG_REQS=3; see sync.py


# ---------------------------------------------------------------------------
# Signed HTTP (same recipe as staffline/smoke_test.py)
# ---------------------------------------------------------------------------

def _sign(ts: str, body: bytes) -> str:
    return hmac.new(HMAC_SECRET.encode(), ts.encode() + b"." + body, hashlib.sha256).hexdigest()


def _request(base: str, path: str, method: str = "GET", body: bytes | None = None):
    body = body or b""
    ts = str(int(time.time()))
    headers = {
        "X-SL-Token": APP_TOKEN,
        "X-SL-Timestamp": ts,
        "X-SL-Signature": _sign(ts, body),
    }
    if body:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base + path, data=body if body else None, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw, status = resp.read(), resp.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    parsed = json.loads(raw) if raw else None
    return status, parsed


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start(env_overrides: dict[str, str]) -> tuple[subprocess.Popen, str]:
    port = _free_port()
    env = dict(os.environ)
    env.update({
        "PYTHONPATH": str(VENDOR_SRC),
        "CHECKPOINT": "0",
        "VENDOR_SEED": "3000",
        "SL_APP_TOKEN": APP_TOKEN,
        "SL_HMAC_SECRET": HMAC_SECRET,
        "REQUEST_LOG_PATH": "/tmp/ib_task0015_fixtures_requests.jsonl",
        "TOKEN_LOG_PATH": "/tmp/ib_task0015_fixtures_tokens.jsonl",
    })
    env.update(env_overrides)
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "staffline.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(VENDOR_SRC.parent), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15
    while time.time() < deadline:
        if proc.poll() is not None:
            sys.exit(f"staffline subprocess exited early (code {proc.returncode})")
        try:
            with urllib.request.urlopen(base + "/", timeout=2) as r:
                if r.status == 200:
                    return proc, base
        except Exception:
            time.sleep(0.3)
    proc.terminate()
    sys.exit(f"staffline subprocess at {base} never became healthy")


def _stop(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


# ---------------------------------------------------------------------------
# Gold-shaped drive: submit once, retry transients, reconcile every 201
# ---------------------------------------------------------------------------

def _drive_batch(base: str, batch_items: list[dict]) -> dict[str, dict]:
    """Submit the whole batch, retry ONLY transient (503) items by the same
    client_ref, then reconcile every tentative 201 by polling GET-by-id up to
    RECONCILE_MAX_ROUNDS times. Returns {client_ref: {"created": bool, "id": str|None}}."""
    resolved: dict[str, dict] = {}
    pending_201: dict[str, str] = {}

    to_submit = list(batch_items)
    while to_submit:
        status, body = _request(base, "/svc/candidates/bulk", "POST", json.dumps({"items": to_submit}).encode())
        assert status == 207, f"bulk create -> {status}: {body}"
        by_ref = {i["client_ref"]: i for i in to_submit}
        retry_next = []
        for r in body["results"]:
            ref = r["client_ref"]
            code = r["status"]
            if code == 201:
                pending_201[ref] = r["id"]
            elif code == 422:
                resolved[ref] = {"created": False, "id": None}
            elif code == 503:
                retry_next.append(by_ref[ref])
            elif code == 409:
                if ref not in resolved and ref not in pending_201:
                    resolved[ref] = {"created": True, "id": None}
        to_submit = retry_next

    rounds = 0
    while pending_201 and rounds < RECONCILE_MAX_ROUNDS:
        rounds += 1
        for ref, cid in list(pending_201.items()):
            status, body = _request(base, f"/svc/candidates/{cid}")
            if status == 200 and isinstance(body, dict) and body.get("id") == cid:
                resolved[ref] = {"created": True, "id": cid}
                del pending_201[ref]
    for ref, cid in pending_201.items():
        # RAW_LAG_REQS-worth of reads elapsed with no sign of it: provably
        # fabricated, never merely delayed.
        resolved[ref] = {"created": False, "id": cid}

    return resolved


def generate(name: str, env_overrides: dict[str, str], *, lying_ref: str | None) -> None:
    batch = json.loads(BATCH_FILE.read_text(encoding="utf-8"))["items"]
    proc, base = _start(env_overrides)
    try:
        resolved = _drive_batch(base, batch)
    finally:
        _stop(proc)

    created_refs = sorted(ref for ref, r in resolved.items() if r["created"])
    failed_refs = sorted(ref for ref, r in resolved.items() if not r["created"])
    fixture: dict = {"created_refs": created_refs, "failed_refs": failed_refs}
    if lying_ref is not None:
        fixture["lying_ref"] = lying_ref
        assert resolved[lying_ref]["created"] is False, "lying ref must resolve to created=false"
        assert resolved[lying_ref]["id"] is not None, "lying ref must have received a fabricated id"

    out = FIXTURES_DIR / f"{name}.json"
    out.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{name}: created={created_refs} failed={failed_refs} -> {out}")


if __name__ == "__main__":
    generate("bulk_ingest_mixed_results", {"SL_BULK_ENABLED": "1"}, lying_ref=None)
    generate(
        "lying_success_reconcile",
        {"SL_BULK_ENABLED": "1", "SL_LYING_REF": LYING_REF, "SL_RAW_LAG_REQS": str(RAW_LAG_REQS)},
        lying_ref=LYING_REF,
    )

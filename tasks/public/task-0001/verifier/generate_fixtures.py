#!/usr/bin/env python3
"""Generate task-0001's verifier fixtures against a real, in-process StaffLine
instance -- never hand-authored.

Boots ``staffline.main:app`` via a plain ``uvicorn`` subprocess against the
canonical monorepo vendor source (no Docker, no build, an ephemeral free port), once at
CHECKPOINT=0 and once at CHECKPOINT=1, and drives it with fully HMAC-signed
requests -- every single request, GET included, carries a freshly computed
X-SL-Token / X-SL-Timestamp / X-SL-Signature triple, exactly as gold's
StafflineClient does (staffline_sync/client.py). This script does not write
or modify anything under vendors/staffline -- it only imports and runs it.

The in-memory bookkeeping below (``Records``) mirrors gold's actual postgres
operations one-for-one (upsert on every fetched row, tombstone-patches
is_deleted/updated_at without touching `data`, never advances a per-entity
watermark from a full crawl) so the emitted fixtures are exactly what gold's
canonical.records table would dump, not a hand-derived approximation.

Run:  python3 generate_fixtures.py
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
from typing import Any

_DEFAULT_VENDOR_SRC = Path(__file__).resolve().parents[4] / "vendors" / "staffline" / "src"
STAFFLINE_SRC = Path(os.environ.get("STAFFLINE_VENDOR_SRC", str(_DEFAULT_VENDOR_SRC)))
if not STAFFLINE_SRC.is_dir():
    raise SystemExit(
        f"task-0001 fixture regeneration requires StaffLine source at {STAFFLINE_SRC}; "
        "set STAFFLINE_VENDOR_SRC to override the monorepo default"
    )
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

APP_TOKEN = "sl-test-app-token"
HMAC_SECRET = "sl-test-hmac-secret"

PAGE_SIZE = 50
ENTITY_PATHS = {
    "candidate": "/svc/candidates",
    "job": "/svc/jobs",
    "application": "/svc/applications",
    "note": "/svc/notes",
}
FILENAMES = {
    "candidate": "candidates",
    "job": "jobs",
    "application": "applications",
    "note": "notes",
}

_signed_request_count = 0


# --- signing (byte-identical recipe to staffline_sync/client.py's gold) ----


def _sign(ts: str, body: bytes) -> str:
    msg = ts.encode("utf-8") + b"." + body
    return hmac.new(HMAC_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def signed_get(base: str, path: str, params: dict[str, Any]) -> tuple[int, Any]:
    global _signed_request_count
    from urllib.parse import urlencode

    url = f"{base}{path}?{urlencode(params)}" if params else f"{base}{path}"
    ts = str(int(time.time()))
    headers = {
        "X-SL-Token": APP_TOKEN,
        "X-SL-Timestamp": ts,
        "X-SL-Signature": _sign(ts, b""),
    }
    _signed_request_count += 1
    req = urllib.request.Request(url, method="GET", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as err:
        raw = err.read()
        return err.code, (json.loads(raw) if raw else None)


def drain(base: str, path: str, extra: dict[str, Any]) -> list[dict]:
    out: list[dict] = []
    start = 0
    while True:
        params = {"start": start, "count": PAGE_SIZE, **extra}
        status, body = signed_get(base, path, params)
        assert status == 200, f"unexpected {status} from {path}: {body!r}"
        out.extend(body["rows"])
        if not body["more"]:
            return out
        start += PAGE_SIZE


# --- subprocess lifecycle (no Docker; ephemeral port; canonical source) ---


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_staffline(checkpoint: int, log_dir: Path) -> tuple[subprocess.Popen, str]:
    log_dir.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(STAFFLINE_SRC),
            "CHECKPOINT": str(checkpoint),
            "VENDOR_SEED": "3000",
            "SL_APP_TOKEN": APP_TOKEN,
            "SL_HMAC_SECRET": HMAC_SECRET,
            "REQUEST_LOG_PATH": str(log_dir / "requests.jsonl"),
            "TOKEN_LOG_PATH": str(log_dir / "tokens.jsonl"),
        }
    )
    # SL_BULK_ENABLED / SL_LYING_REF / SL_RAW_LAG_REQS deliberately absent --
    # this task never turns the bulk-207 surface on (that's task-0015's).
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "staffline.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 15.0
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
    sys.exit(f"staffline at {base} never became healthy")


def stop_staffline(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


# --- canonical shaping (byte-identical to staffline_sync/store.py's gold) --


def to_canonical(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_id": row["id"],
        "data": {k: v for k, v in row.items() if k != "id"},
        "updated_at": int(row.get("mod_ts", 0)),
        "is_deleted": False,
    }


class Records:
    """In-memory stand-in for gold's `canonical.records` postgres table --
    same upsert/tombstone semantics, so the dumped fixtures are exactly what
    gold's own dump_all() would write."""

    def __init__(self) -> None:
        self._by_entity: dict[str, dict[str, dict[str, Any]]] = {e: {} for e in ENTITY_PATHS}

    def upsert(self, entity: str, canonical_row: dict[str, Any]) -> None:
        self._by_entity[entity][canonical_row["source_id"]] = canonical_row

    def tombstone(self, entity: str, source_id: str, deleted_at: int) -> None:
        existing = self._by_entity[entity].get(source_id)
        if existing is None:
            self._by_entity[entity][source_id] = {
                "source_id": source_id, "data": {}, "updated_at": deleted_at, "is_deleted": True,
            }
        else:
            existing["is_deleted"] = True
            existing["updated_at"] = deleted_at

    def dump(self, entity: str) -> list[dict[str, Any]]:
        return sorted(self._by_entity[entity].values(), key=lambda r: r["source_id"])


def sync_pass(base: str, records: Records) -> None:
    """Exactly gold's unfiltered path (watermark=0 case): every entity kind,
    include_stage=1 on applications, then a since=0 tombstone sweep."""
    for entity, path in ENTITY_PATHS.items():
        extra = {"include_stage": 1} if entity == "application" else {}
        for row in drain(base, path, extra):
            records.upsert(entity, to_canonical(row))

    for tomb in drain(base, "/svc/tombstones", {"since": 0}):
        entity = tomb["entity"]
        if entity in ENTITY_PATHS:
            records.tombstone(entity, tomb["id"], int(tomb["deleted_at"]))


def write_fixture(checkpoint: int, entity: str, rows: list[dict[str, Any]]) -> Path:
    path = FIXTURES_DIR / f"{FILENAMES[entity]}_checkpoint_{checkpoint}.json"
    path.write_text(json.dumps(rows, indent=2), encoding="utf-8")
    return path


def main() -> None:
    if not STAFFLINE_SRC.is_dir():
        sys.exit(f"staffline source not found at {STAFFLINE_SRC}")
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    records = Records()

    print("== booting staffline at CHECKPOINT=0 (ephemeral port, in-process, no Docker) ==")
    proc0, base0 = start_staffline(0, FIXTURES_DIR / "_gen_logs_cp0")
    try:
        sync_pass(base0, records)
    finally:
        stop_staffline(proc0)

    for entity in ENTITY_PATHS:
        rows = records.dump(entity)
        path = write_fixture(0, entity, rows)
        print(f"  wrote {path.name}: {len(rows)} rows")

    print("== booting staffline at CHECKPOINT=1 (mutation timeline applied) ==")
    proc1, base1 = start_staffline(1, FIXTURES_DIR / "_gen_logs_cp1")
    try:
        sync_pass(base1, records)
    finally:
        stop_staffline(proc1)

    for entity in ENTITY_PATHS:
        rows = records.dump(entity)
        path = write_fixture(1, entity, rows)
        print(f"  wrote {path.name}: {len(rows)} rows")

    print(f"\ntotal signed requests sent: {_signed_request_count}")

    cands = {r["source_id"]: r for r in records.dump("candidate")}
    apps = {r["source_id"]: r for r in records.dump("application")}
    print("\n-- named-id transcript (checkpoint 1 canonical state) --")
    print("cand_0042:", json.dumps(cands.get("cand_0042"), indent=2))
    print("cand_0900:", json.dumps(cands.get("cand_0900"), indent=2))
    print("cand_0017:", json.dumps(cands.get("cand_0017"), indent=2))
    print("app_0005:", json.dumps(apps.get("app_0005"), indent=2))

    # Clean up the throwaway per-boot request/token logs -- they exist only
    # so a human running this script by hand can inspect them; the verifier
    # fixtures themselves don't reference them.
    import shutil

    shutil.rmtree(FIXTURES_DIR / "_gen_logs_cp0", ignore_errors=True)
    shutil.rmtree(FIXTURES_DIR / "_gen_logs_cp1", ignore_errors=True)


if __name__ == "__main__":
    main()

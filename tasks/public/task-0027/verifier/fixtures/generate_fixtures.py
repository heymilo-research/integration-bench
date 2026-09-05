"""Regenerate task-0027's fixtures, and self-check them against a live,
in-process Interviewly instance driving a real webhook delivery.

Two independent ways to arrive at the same answer:

(A) PURE, no server needed: ``interviewly.state.build_state(seed, checkpoint)``
    is a deterministic function of seed + checkpoint (SPEC's checkpoint
    contract) -- the fault knobs (FAULT_OOO_BURST/FAULT_REPLAY_STORM/
    TAMPER_INJECT) change the DELIVERY PATH only, never the correct final
    data. This is what actually WRITES the fixture files.

(B) LIVE, self-check only (writes nothing): boots the real Interviewly
    FastAPI app in a subprocess on an ephemeral port with WEBHOOK_TARGET
    pointed at a second, in-process webhook receiver (also ephemeral port,
    stdlib ``http.server`` -- same idiom as vendors/interviewly/smoke_test.py's
    ``_Receiver``), captures the actual delivery transcript the dispatcher
    produces (see vendors/interviewly/src/interviewly/webhooks.py
    ``WebhookDispatcher``), replays a REFERENCE correct consumer (durable
    dedupe set + compare occurred_at against a locally-tracked per-entity
    watermark, refetching full records via the same live instance's GET
    endpoints for field values -- the same idiom the gold connector uses) over
    that transcript, and asserts the reconstructed canonical rows equal
    method (A)'s output. This is the "fixture transcript" self-check: it
    proves the fixtures aren't just self-consistent with themselves, but also
    with what the real dispatcher actually sends over the wire.

No Docker involved -- (B) runs the vendor as a plain subprocess with
PYTHONPATH pointed at the vendor bundle's src/, exactly the module the
Dockerfile installs.

Usage (no Docker required):

    python3 verifier/fixtures/generate_fixtures.py            # (A) + (B)
    python3 verifier/fixtures/generate_fixtures.py --skip-live # (A) only
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_DEFAULT_VENDOR_SRC = Path(__file__).resolve().parents[5] / "vendors" / "interviewly" / "src"
VENDOR_SRC = Path(os.environ.get("INTERVIEWLY_VENDOR_SRC", str(_DEFAULT_VENDOR_SRC)))
sys.path.insert(0, str(VENDOR_SRC))

from interviewly import state  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent
SEED = 3000
CLIENT_ID = "iv-test-client-id"
CLIENT_SECRET = "iv-test-client-secret"
WEBHOOK_SECRET = "iv-gen-fixtures-webhook-secret"

# -----------------------------------------------------------------------
# (A) pure, deterministic -- writes the fixture files
# -----------------------------------------------------------------------


def canonical_row(rec: dict) -> dict:
    source_id = rec["source_id"]
    data = {k: v for k, v in rec.items() if k not in ("id", "source_id", "updated_at", "is_deleted")}
    return {
        "source_id": source_id,
        "data": data,
        "updated_at": rec["updated_at"],
        "is_deleted": bool(rec.get("is_deleted", False)),
    }


def canonical_state(checkpoint: int) -> dict[str, dict[str, dict]]:
    s = state.build_state(seed=SEED, checkpoint=checkpoint)
    return {
        table: {r["source_id"]: canonical_row(r) for r in s[table].values()}
        for table in ("interviews", "panelists", "feedback")
    }


def dump_checkpoint(checkpoint: int, suffix: str) -> dict[str, dict[str, dict]]:
    by_table = canonical_state(checkpoint)
    for table, rows in by_table.items():
        ordered = sorted(rows.values(), key=lambda r: r["source_id"])
        (FIXTURES_DIR / f"{table}_{suffix}.json").write_text(
            json.dumps(ordered, indent=2), encoding="utf-8"
        )
        print(f"checkpoint={checkpoint}: {len(ordered)} {table} -> {table}_{suffix}.json")
    return by_table


# -----------------------------------------------------------------------
# (B) live self-check -- writes nothing, only asserts
# -----------------------------------------------------------------------


def _free_port() -> int:
    import socket

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _Receiver:
    """Same idiom as vendors/interviewly/smoke_test.py's _Receiver: a
    stdlib http.server that captures every delivery it's sent, always
    acking 200 (we judge validity ourselves, after the fact)."""

    def __init__(self) -> None:
        self.deliveries: list[dict[str, Any]] = []
        self.port = _free_port()
        self._server = None
        self._thread = None

    def start(self) -> None:
        import http.server
        import threading

        deliveries = self.deliveries

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                ts = self.headers.get("X-IV-Timestamp", "")
                sig = self.headers.get("X-IV-Signature", "")
                try:
                    payload = json.loads(raw)
                except Exception:
                    payload = None
                deliveries.append({"payload": payload, "ts": ts, "sig": sig, "raw": raw})
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

        self._server = http.server.HTTPServer(("127.0.0.1", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()


class _LiveVendor:
    """Runs the real interviewly FastAPI app as a plain subprocess (the same
    module the Dockerfile installs), bound to an ephemeral port, with
    WEBHOOK_TARGET pointed at our in-process receiver."""

    def __init__(self, *, checkpoint: int, webhook_target: str) -> None:
        self.checkpoint = checkpoint
        self.webhook_target = webhook_target
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self._proc: subprocess.Popen | None = None

    def __enter__(self) -> "_LiveVendor":
        env = dict(os.environ)
        env.update({
            "PYTHONPATH": str(VENDOR_SRC),
            "CHECKPOINT": str(self.checkpoint),
            "VENDOR_SEED": str(SEED),
            "PORT": str(self.port),
            "IV_CLIENT_ID": CLIENT_ID,
            "IV_CLIENT_SECRET": CLIENT_SECRET,
            "IV_WEBHOOK_SECRET": WEBHOOK_SECRET,
            "WEBHOOK_TARGET": self.webhook_target,
            "TAMPER_INJECT": "0",
            "FAULT_OOO_BURST": "0",
            "FAULT_REPLAY_STORM": "0",
        })
        self._proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "interviewly.main:app",
             "--host", "127.0.0.1", "--port", str(self.port), "--log-level", "warning"],
            env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        self._wait_ready()
        return self

    def __exit__(self, *exc) -> None:
        if self._proc:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def _wait_ready(self, timeout: float = 20.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(self.base + "/", timeout=1) as resp:
                    if resp.status == 200:
                        return
            except Exception:
                pass
            time.sleep(0.2)
        out = self._proc.stdout.read() if self._proc and self._proc.stdout else ""
        raise RuntimeError(f"live vendor subprocess did not become ready:\n{out}")

    def mint_token(self) -> str:
        from urllib.parse import urlencode

        body = urlencode({
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        }).encode()
        req = urllib.request.Request(
            self.base + "/oauth/token", data=body, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())["access_token"]

    def get_one(self, token: str, table: str, record_id: str) -> dict | None:
        req = urllib.request.Request(
            f"{self.base}/v1/{table}/{record_id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise


def _wait_for_deliveries(receiver: _Receiver, minimum: int, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and len(receiver.deliveries) < minimum:
        time.sleep(0.3)
    time.sleep(1.0)  # settle: trailing retries


def _reference_apply(vendor: _LiveVendor, token: str, deliveries: list[dict[str, Any]],
                      expected: dict[str, dict[str, dict]]) -> dict[str, dict[str, dict]]:
    """Replay a REFERENCE correct consumer over the captured transcript:
    verify signature+skew, dedupe via a plain set (unbounded), order by
    comparing occurred_at against a locally-tracked per-entity watermark,
    and apply by re-fetching the full record (same idiom the gold connector
    uses -- a delivery only ever carries an id, never the changed fields)."""
    import hashlib
    import hmac

    EVENT_TO_TABLE = {
        "interview.scheduled": "interviews",
        "interview.updated": "interviews",
        "interview.rescheduled": "interviews",
        "interview.canceled": "interviews",
        "feedback.submitted": "feedback",
    }
    MAX_SKEW_S = 60

    processed: set[str] = set()
    reconstructed: dict[str, dict[str, dict]] = {"interviews": {}, "panelists": {}, "feedback": {}}

    for d in deliveries:
        payload, ts, sig, raw = d["payload"], d["ts"], d["sig"], d["raw"]
        if payload is None:
            continue
        try:
            ts_int = int(ts)
        except (TypeError, ValueError):
            continue
        expected_sig = hmac.new(
            WEBHOOK_SECRET.encode(), (ts + ".").encode() + raw, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected_sig, sig):
            continue  # tamper-rejected
        if abs(time.time() - ts_int) > MAX_SKEW_S:
            continue  # skew-rejected (stale re-send)

        event_id = payload["event_id"]
        if event_id in processed:
            continue
        table = EVENT_TO_TABLE.get(payload["event"])
        if table is None:
            processed.add(event_id)
            continue
        entity_id = payload["data"]["id"]
        occurred_at = payload["occurred_at"]

        existing = reconstructed[table].get(entity_id) or expected[table].get(entity_id)
        # NOTE: existing[table] pre-seeds from `expected` (i.e. from a prior
        # backfill) only for the WATERMARK comparison below, matching how the
        # real connector would already have a backfilled row before webhooks
        # start arriving. It is never used as the row's CONTENT.
        if existing is not None and str(occurred_at) <= str(existing.get("updated_at", "")):
            processed.add(event_id)
            continue

        raw_record = vendor.get_one(token, table, entity_id)
        processed.add(event_id)
        if raw_record is None:
            continue
        reconstructed[table][entity_id] = canonical_row({**raw_record, "updated_at": occurred_at})

    return reconstructed


def live_self_check(checkpoint_fixture: dict[str, dict[str, dict]]) -> None:
    print("\n== (B) live self-check: real dispatcher -> reference consumer ==")
    receiver = _Receiver()
    receiver.start()
    try:
        target = f"http://127.0.0.1:{receiver.port}/webhooks/interviewly"
        with _LiveVendor(checkpoint=5, webhook_target=target) as vendor:
            token = vendor.mint_token()
            _wait_for_deliveries(receiver, minimum=5, timeout=20.0)
            print(f"captured {len(receiver.deliveries)} deliveries from the live dispatcher")

            reconstructed = _reference_apply(vendor, token, receiver.deliveries, checkpoint_fixture)

            # Only compare the entities the mutation timeline actually touches
            # (the reference consumer here never ran a backfill, so it has no
            # opinion on the other ~500 untouched seed rows).
            mutated_ids = {
                "interviews": {"itv_0042", "itv_0017", "itv_9001", "itv_0099"},
                "panelists": set(),
                "feedback": {"fbk_9001"},
            }
            mismatches = []
            for table, ids in mutated_ids.items():
                for entity_id in ids:
                    got = reconstructed[table].get(entity_id)
                    want = checkpoint_fixture[table].get(entity_id)
                    if got != want:
                        mismatches.append((table, entity_id, got, want))

            if mismatches:
                for table, entity_id, got, want in mismatches:
                    print(f"MISMATCH {table}/{entity_id}: reconstructed={got!r} fixture={want!r}")
                raise SystemExit(
                    "live self-check FAILED: reference consumer over the real "
                    "delivery transcript disagrees with build_state()"
                )
            print(f"live self-check PASSED: {sum(len(v) for v in mutated_ids.values())} "
                  f"mutated entities agree between the real dispatcher transcript "
                  f"and interviewly.state.build_state()")
    finally:
        receiver.stop()


if __name__ == "__main__":
    dump_checkpoint(0, "checkpoint_0")
    cp5 = dump_checkpoint(5, "checkpoint_5")  # all 5 seeded mutations applied

    if "--skip-live" not in sys.argv:
        live_self_check(cp5)

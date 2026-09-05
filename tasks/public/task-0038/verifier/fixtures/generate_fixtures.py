"""Regenerate task-0038's fixtures (all 4 entities, checkpoints 0 and 5).

Fully deterministic (no Docker needed for the core fixtures) via
``talentloop.state.build_state(seed=3015, checkpoint=N)`` -- the final
canonical-store shape for a mutation is identical regardless of HOW it was
discovered (webhook ack, poll reconcile, or dead-letter-queue recovery), so
these fixtures serve both this task's scenarios unchanged from the rest of
the talentloop family's convention (see task-0023/0024's generators for the
shared rationale on why a deleted row preserves its last-known
``data``/``updated_at`` unchanged).

Usage:

    python3 verifier/fixtures/generate_fixtures.py          # core fixtures only
    python3 verifier/fixtures/generate_fixtures.py --probe-ack-recipe
        Additionally boots the real talentloop:local image against a small,
        local ack-echoing HTTP receiver (ephemeral ports, no compose needed --
        the same shape as vendors/talentloop/smoke_test.py's `_AckReceiver`)
        to INDEPENDENTLY confirm, from the real HTTP headers the vendor sends,
        that docs/webhooks.md's ack-token recipe --
        ``hex(hmac_sha256(secret, event_id + "." + X-TL-Timestamp))`` -- is
        exactly what the shipped image implements, and writes that evidence
        to ``ack_recipe_probe.json``. Requires Docker; this is an authoring-
        time confidence check (conduct-rules.md gate 4/5 discoverability
        spirit applied to a truthful, non-lie feature), not something the
        live verifier scenarios depend on -- the per-attempt header pair this
        probe needs is not part of the vendor's on-disk delivery log, so it
        cannot be reproduced by the graded scenarios themselves.

Regenerates candidates/jobs/applications/notes fixtures at checkpoint 0 and
checkpoint 5 (the mutation timeline's full extent -- cand_0007 delete,
job_0003 status update, note_0004 body update, cand_0055 pipeline_status
update, and app_0009 delete).
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

_DEFAULT_VENDOR_SRC = Path(__file__).resolve().parents[5] / "vendors" / "talentloop" / "src"
VENDOR_SRC = Path(os.environ.get("TALENTLOOP_VENDOR_SRC", str(_DEFAULT_VENDOR_SRC)))
sys.path.insert(0, str(VENDOR_SRC))

from talentloop import state  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent
SEED = 3015
IMAGE = "talentloop:local"
WEBHOOK_SECRET = "tl-fixtures-probe-secret"

_PLURALS = ("candidates", "jobs", "applications", "notes")


def canonical(rec: dict, *, is_deleted: bool = False) -> dict:
    source_id = rec.get("source_id") or rec["id"]
    updated_at = str(rec["modified_at"])
    data = {k: v for k, v in rec.items() if k != "source_id"}
    return {"source_id": source_id, "data": data, "updated_at": updated_at, "is_deleted": is_deleted}


def dump_checkpoint(checkpoint: int, filenames: dict[str, str], *, base_records: dict | None = None) -> None:
    app_state, deleted = state.build_state(seed=SEED, checkpoint=checkpoint)

    for plural, fname in filenames.items():
        rows = [canonical(r) for r in app_state[plural].values()]
        base = (base_records or {}).get(plural, {})
        for eid in deleted[plural]:
            base_rec = base.get(eid)
            if base_rec is not None:
                rows.append(canonical(base_rec, is_deleted=True))
        rows.sort(key=lambda r: r["source_id"])
        (FIXTURES_DIR / fname).write_text(json.dumps(rows, indent=2, sort_keys=True), encoding="utf-8")
        print(f"checkpoint={checkpoint}: {len(rows)} {plural} -> {fname}")


# ---------------------------------------------------------------------------
# Optional: independently confirm the ack-token recipe against a real boot.
# ---------------------------------------------------------------------------

def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


class _AckProbeReceiver:
    """Minimal ack-echoing receiver (mirrors smoke_test.py's `_AckReceiver`,
    mode="ack"): captures the raw X-TL-Timestamp/X-TL-Ack-Token headers of the
    first delivery, echoes the token back (so the vendor doesn't retry), and
    stops there -- exactly enough evidence to recompute the recipe ourselves.
    """

    def __init__(self) -> None:
        self.port = _free_port()
        self.captured: dict | None = None
        self._server: http.server.HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        captured_holder = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *_a):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                ts = self.headers.get("X-TL-Timestamp", "")
                ack_token = self.headers.get("X-TL-Ack-Token", "")
                try:
                    payload = json.loads(raw)
                except Exception:
                    payload = None
                if captured_holder.captured is None and payload is not None:
                    captured_holder.captured = {
                        "event_id": payload.get("event_id"),
                        "timestamp": ts,
                        "ack_token": ack_token,
                    }
                self.send_response(200)
                body = json.dumps({"ack": ack_token}).encode("utf-8")
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self._server = http.server.HTTPServer(("0.0.0.0", self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()


def probe_ack_recipe() -> None:
    recv = _AckProbeReceiver()
    recv.start()
    target = f"http://host.docker.internal:{recv.port}/webhooks/talentloop"
    name = "tl-fixtures-ack-probe"
    subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
    try:
        subprocess.run(
            [
                "docker", "run", "-d", "--name", name,
                "--add-host", "host.docker.internal:host-gateway",
                "-e", "CHECKPOINT=1",
                "-e", "VENDOR_SEED=3015",
                "-e", "TL_CLIENT_ID=tl-test-client-id",
                "-e", "TL_CLIENT_SECRET=tl-test-client-secret",
                "-e", f"TL_WEBHOOK_SECRET={WEBHOOK_SECRET}",
                "-e", f"WEBHOOK_TARGET={target}",
                "-e", "TL_ACK_REQUIRED=1",
                IMAGE,
            ],
            capture_output=True, check=True,
        )
        deadline = time.monotonic() + 20.0
        while recv.captured is None and time.monotonic() < deadline:
            time.sleep(0.5)
    finally:
        recv.stop()
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)

    if recv.captured is None:
        print("probe_ack_recipe: no delivery captured within timeout -- skipping "
              "(is Docker available and is talentloop:local built?)", file=sys.stderr)
        return

    event_id = recv.captured["event_id"]
    ts = recv.captured["timestamp"]
    observed_token = recv.captured["ack_token"]
    expected_token = hmac.new(
        WEBHOOK_SECRET.encode("utf-8"),
        f"{event_id}.{ts}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    assert observed_token == expected_token, (
        f"ack recipe mismatch: observed={observed_token!r} expected={expected_token!r} "
        f"-- docs/webhooks.md's documented recipe does not match the shipped image"
    )

    out = {
        "event_id": event_id,
        "timestamp": ts,
        "ack_token": observed_token,
        "recipe": "hex(hmac_sha256(secret, event_id + '.' + X-TL-Timestamp))",
        "note": "captured live against talentloop:local; secret is a fixtures-only "
                "test value, not the sandbox secret used by any task's compose file",
    }
    (FIXTURES_DIR / "ack_recipe_probe.json").write_text(
        json.dumps(out, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(f"probe_ack_recipe: confirmed -- wrote ack_recipe_probe.json ({out})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--probe-ack-recipe", action="store_true",
                        help="also confirm the ack recipe against a real docker boot")
    args = parser.parse_args()

    cp0_state, _cp0_deleted = state.build_state(seed=SEED, checkpoint=0)
    dump_checkpoint(0, {
        "candidates": "candidates_checkpoint_0.json",
        "jobs": "jobs_checkpoint_0.json",
        "applications": "applications_checkpoint_0.json",
        "notes": "notes_checkpoint_0.json",
    })
    dump_checkpoint(5, {
        "candidates": "candidates_post_cp2.json",
        "jobs": "jobs_post_cp2.json",
        "applications": "applications_post_cp2.json",
        "notes": "notes_post_cp2.json",
    }, base_records=cp0_state)

    if args.probe_ack_recipe:
        probe_ack_recipe()

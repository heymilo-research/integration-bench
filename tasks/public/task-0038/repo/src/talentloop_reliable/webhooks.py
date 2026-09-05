"""Webhook listener. See ``PROBLEM.md``."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import sqlite3

from talentloop_reliable import store  # noqa: F401
from talentloop_reliable.client import TalentLoopClient  # noqa: F401
from talentloop_reliable.config import Config
from talentloop_reliable.sync import apply_application, apply_candidate, mark_deleted  # noqa: F401

MAX_SKEW_S = 300
_DEDUP_PREFIX = "processed_event:"

_CANDIDATE_EVENTS = {"candidate.created", "candidate.updated", "candidate.deleted"}
_APPLICATION_EVENTS = {"application.created", "application.updated", "application.deleted"}
_DELETE_EVENTS = {"candidate.deleted", "application.deleted"}


class _Applier:
    """Shared state the handler and the bounded-serve loop use."""

    def __init__(self, cfg: Config, conn: sqlite3.Connection) -> None:
        self.cfg = cfg
        self.conn = conn
        self.client = TalentLoopClient(cfg)
        self.client.authenticate()
        self.applied_events = 0
        self.last_delivery_at = time.monotonic()
        self._lock = threading.Lock()

    def handle(self, raw_body: bytes, timestamp: str, signature: str) -> tuple[int, bytes]:
        """Process one delivery. Returns ``(status, response_body_bytes)``.

        Return ``(status, response_body_bytes)`` for this delivery.
        """
        with self._lock:
            self.last_delivery_at = time.monotonic()
        return 200, b""


def _make_handler(applier: "_Applier"):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):  # silence default stderr logging
            pass

        def do_POST(self):  # noqa: N802
            if self.path.rstrip("/") != "/webhooks/talentloop":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length else b""
            ts = self.headers.get("X-TL-Timestamp", "")
            sig = self.headers.get("X-TL-Signature", "")
            status, body = applier.handle(raw, ts, sig)
            self.send_response(status)
            if body:
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_GET(self):  # noqa: N802 - trivial health endpoint
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

    return Handler


def serve(
    cfg: Config,
    conn: sqlite3.Connection,
    *,
    max_events: int | None = None,
    idle_timeout: float | None = None,
    max_runtime: float | None = None,
) -> None:
    applier = _Applier(cfg, conn)
    httpd = HTTPServer((cfg.serve_host, cfg.serve_port), _make_handler(applier))

    if max_events is None and idle_timeout is None and max_runtime is None:
        httpd.serve_forever()
        return

    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    start = time.monotonic()
    try:
        while True:
            time.sleep(0.2)
            now = time.monotonic()
            with applier._lock:
                applied = applier.applied_events
                last = applier.last_delivery_at
            if max_events is not None and applied >= max_events:
                break
            if idle_timeout is not None and (now - last) >= idle_timeout:
                break
            if max_runtime is not None and (now - start) >= max_runtime:
                break
    finally:
        httpd.shutdown()
        httpd.server_close()

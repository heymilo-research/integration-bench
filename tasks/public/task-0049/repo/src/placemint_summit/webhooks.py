"""Webhook listener: HMAC verify + dedup/apply. See ``PROBLEM.md``."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from placemint_summit.client import PlacemintClient
from placemint_summit.config import Config
from placemint_summit.store import Store

MAX_SKEW_S = 300

_ENTITY_PATH = {
    "placement": "/api/placements/{id}",
    "client": "/api/clients/{id}",
    "note": "/api/notes/{id}",
}


def _verify(secret: str, timestamp: str, signature: str, raw_body: bytes) -> bool:
    """Return True iff the delivery is authentic and within skew tolerance."""
    if not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts) > MAX_SKEW_S:
        return False
    mac = hmac.new(
        secret.encode("utf-8"),
        (str(timestamp) + ".").encode("utf-8") + raw_body,
        hashlib.sha256,
    )
    expected = mac.hexdigest()
    return hmac.compare_digest(expected, signature)


class _Applier:
    """Shared state the HTTP handler and (optionally) tests use."""

    def __init__(self, cfg: Config, client: PlacemintClient, store: Store) -> None:
        self.cfg = cfg
        self.client = client
        self.store = store
        self.applied_events = 0
        self.last_delivery_at = time.monotonic()
        self._lock = threading.Lock()

    def handle(self, raw_body: bytes, timestamp: str, signature: str) -> int:
        """Process one delivery. Returns the HTTP status to send back."""
        with self._lock:
            self.last_delivery_at = time.monotonic()
        raise NotImplementedError


def _make_handler(applier: "_Applier"):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):  # silence default stderr logging
            pass

        def do_POST(self):  # noqa: N802
            if self.path.rstrip("/") != "/webhooks/placemint":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length else b""
            ts = self.headers.get("X-PM-Timestamp", "")
            sig = self.headers.get("X-PM-Signature", "")
            status = applier.handle(raw, ts, sig)
            self.send_response(status)
            self.end_headers()
            self.wfile.write(b"ok" if 200 <= status < 300 else b"rejected")

        def do_GET(self):  # noqa: N802 - trivial health endpoint
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

    return Handler


def start_listener(cfg: Config, client: PlacemintClient, store: Store) -> tuple[HTTPServer, _Applier]:
    """Start the webhook HTTP server in a background thread. Returns the
    server (for shutdown) and the applier (for introspection/testing)."""
    applier = _Applier(cfg, client, store)
    httpd = HTTPServer((cfg.serve_host, cfg.serve_port), _make_handler(applier))
    thread = threading.Thread(target=httpd.serve_forever, name="pm-webhook-listener", daemon=True)
    thread.start()
    return httpd, applier

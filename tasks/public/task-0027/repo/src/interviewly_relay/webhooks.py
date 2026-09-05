"""Webhook listener for ``POST /webhooks/interviewly`` (provided). See ``PROBLEM.md``."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, HTTPServer

from interviewly_relay.client import InterviewlyClient
from interviewly_relay.config import Config
from interviewly_relay.store import Store
from interviewly_relay.sync import apply_event

MAX_SKEW_S = 60
EVENT_TO_TABLE = {
    "interview.scheduled": "interviews",
    "interview.updated": "interviews",
    "interview.rescheduled": "interviews",
    "interview.canceled": "interviews",
    "feedback.submitted": "feedback",
}

_PROCESSED_EVENT_IDS_MAXLEN = 64
_PROCESSED_EVENT_IDS_STATE_KEY = "webhooks.processed_event_ids"


class _ProcessedEvents:
    """Durable processed-``event_id`` tracker backed by ``state.json``."""

    def __init__(self, store: Store) -> None:
        self.store = store

    def _load(self) -> deque:
        raw = self.store.get_state(_PROCESSED_EVENT_IDS_STATE_KEY) or []
        return deque(raw, maxlen=_PROCESSED_EVENT_IDS_MAXLEN)

    def contains(self, event_id: str) -> bool:
        return event_id in self._load()

    def add(self, event_id: str) -> None:
        ring = self._load()
        ring.append(event_id)
        self.store.set_state(_PROCESSED_EVENT_IDS_STATE_KEY, list(ring))


def _verify(secret: str, timestamp: str, signature: str, raw_body: bytes) -> bool:
    """Return True if the delivery is authentic and within max skew."""
    if not timestamp or not signature:
        return False
    try:
        ts_int = int(timestamp)
    except ValueError:
        return False
    if abs(time.time() - ts_int) > MAX_SKEW_S:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        (timestamp + ".").encode("utf-8") + raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


class _Applier:
    """Shared state the handler uses across deliveries."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.store = Store(config.output_dir)
        self.client = InterviewlyClient(config)
        self._processed = _ProcessedEvents(self.store)

    def handle(self, raw_body: bytes, timestamp: str, signature: str) -> int:
        """Handle one webhook delivery. Returns HTTP status code."""
        if not _verify(self.config.webhook_secret, timestamp, signature, raw_body):
            return 401

        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return 400

        event_id = payload.get("event_id")
        event = payload.get("event")
        occurred_at = payload.get("occurred_at")
        entity_id = (payload.get("data") or {}).get("id")
        if not event_id or not event or not entity_id:
            return 400

        if self._processed.contains(event_id):
            return 200
        self._processed.add(event_id)

        table = EVENT_TO_TABLE.get(event)
        if table is None:
            return 200

        apply_event(
            self.store,
            self.client,
            table,
            entity_id,
            event_id=event_id,
            occurred_at=occurred_at,
        )
        return 200


def _make_handler(applier: "_Applier"):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):  # silence default stderr logging
            pass

        def do_POST(self):  # noqa: N802
            if self.path.rstrip("/") != "/webhooks/interviewly":
                self.send_response(404)
                self.end_headers()
                return
            length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(length) if length else b""
            ts = self.headers.get("X-IV-Timestamp", "")
            sig = self.headers.get("X-IV-Signature", "")
            status = applier.handle(raw, ts, sig)
            self.send_response(status)
            self.end_headers()
            self.wfile.write(b"ok" if 200 <= status < 300 else b"rejected")

        def do_GET(self):  # noqa: N802 - trivial health endpoint
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

    return Handler


def serve(config: Config) -> None:
    applier = _Applier(config)
    httpd = HTTPServer((config.serve_host, config.serve_port), _make_handler(applier))
    httpd.serve_forever()

"""Webhook listener and writeback confirmation glue. See ``PROBLEM.md``."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from interviewly_writeback.client import InterviewlyClient
from interviewly_writeback.config import Config
from interviewly_writeback.store import Store
from interviewly_writeback.sync import apply_record

MAX_SKEW_S = 60
EVENT_TO_TABLE = {
    "interview.scheduled": "interviews",
    "interview.updated": "interviews",
    "interview.rescheduled": "interviews",
    "interview.canceled": "interviews",
    "feedback.submitted": "feedback",
}
_PROCESSED_STATE_KEY = "webhooks.processed_event_ids"


def _verify(secret: str, timestamp: str, signature: str, raw_body: bytes) -> bool:
    """Return True iff the delivery is authentic and within acceptable skew."""
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

    def _processed_ids(self) -> set[str]:
        return set(self.store.get_state(_PROCESSED_STATE_KEY) or [])

    def _mark_processed(self, event_id: str) -> None:
        ids = self._processed_ids()
        ids.add(event_id)
        self.store.set_state(_PROCESSED_STATE_KEY, sorted(ids))

    def handle(self, raw_body: bytes, timestamp: str, signature: str) -> int:
        """Process one delivery: verify -> dedupe -> order -> fetch+apply -> ack."""
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

        if event_id in self._processed_ids():
            return 200

        table = EVENT_TO_TABLE.get(event)
        if table is None:
            self._mark_processed(event_id)
            return 200

        rows = self.store.load(table)
        existing = rows.get(entity_id)
        if existing is not None and occurred_at and str(occurred_at) <= str(existing.get("updated_at", "")):
            self._mark_processed(event_id)
            return 200

        raw = self.client.get_one(table, entity_id)
        if raw is not None:
            apply_record(self.store, rows, table, raw)
            self.store.write(table, rows)

        if event == "interview.rescheduled":
            from interviewly_writeback.writeback import confirm_from_event

            confirm_from_event(self.store, interview_id=entity_id, event_id=event_id)

        self._mark_processed(event_id)
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


_RECONCILE_INTERVAL_S = 15.0


def _reconcile_loop(applier: "_Applier") -> None:
    """Periodic poll reconciliation for pending writebacks."""
    from interviewly_writeback.writeback import reconcile_pending

    while True:
        time.sleep(_RECONCILE_INTERVAL_S)
        try:
            reconcile_pending(applier.client, applier.store)
        except Exception:
            # Best-effort backstop; a failed reconciliation pass should never
            # crash the listener -- the next pass (or the confirming event
            # itself) will still catch it.
            pass


def serve(config: Config) -> None:
    applier = _Applier(config)
    threading.Thread(target=_reconcile_loop, args=(applier,), daemon=True).start()
    httpd = HTTPServer((config.serve_host, config.serve_port), _make_handler(applier))
    httpd.serve_forever()

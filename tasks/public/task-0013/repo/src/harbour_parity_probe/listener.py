"""The webhook listener.

`python -m harbour_parity_probe serve` runs it. RecruitOS POSTs deliveries to
`http://connector:4000/webhooks/recruitos`; every delivery is verified against
`RO_WEBHOOK_SECRET` before it is written anywhere, and an accepted event is
appended to `STATE_DIR/events.jsonl` for the next parity pass to pick up.

Verification is both halves of the contract in `docs/webhooks.md`: the HMAC over
`timestamp + "." + raw_body` computed on the exact bytes received, AND the
timestamp's distance from our own clock.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from harbour_parity_probe.config import Config

MAX_SKEW_S = 300
EVENTS_FILE = "events.jsonl"


def verify(secret: str, timestamp: str, signature: str, raw_body: bytes) -> bool:
    if not signature or not timestamp:
        return False
    try:
        sent_at = int(timestamp)
    except (TypeError, ValueError):
        return False
    if abs(int(time.time()) - sent_at) > MAX_SKEW_S:
        return False
    expected = hmac.new(
        secret.encode("utf-8"),
        (timestamp + ".").encode("utf-8") + raw_body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def _record(state_dir: Path, payload: dict) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    entry = {
        "event_id": str(payload.get("event_id") or ""),
        "event": str(payload.get("event") or ""),
        "entity_id": str((payload.get("data") or {}).get("id") or ""),
        "occurred_at": str(payload.get("occurred_at") or ""),
    }
    with (state_dir / EVENTS_FILE).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")


def read_events(state_dir: Path) -> list[dict]:
    """Accepted events, de-duplicated by event id, in arrival order."""
    path = Path(state_dir) / EVENTS_FILE
    if not path.is_file():
        return []
    seen: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        seen.setdefault(str(entry.get("event_id")), entry)
    return list(seen.values())


def build_handler(cfg: Config):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_args) -> None:  # noqa: D102 - quiet by design
            return

        def _reply(self, code: int, body: bytes = b"{}") -> None:
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            self._reply(200)

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b""
            signature = self.headers.get("X-ROS-Signature") or ""
            timestamp = self.headers.get("X-ROS-Timestamp") or ""
            if not verify(cfg.webhook_secret, timestamp, signature, raw):
                self._reply(401, b'{"error":"signature_rejected"}')
                return
            try:
                payload = json.loads(raw)
            except ValueError:
                self._reply(400, b'{"error":"bad_json"}')
                return
            _record(cfg.state_dir, payload if isinstance(payload, dict) else {})
            self._reply(200)

    return Handler


def serve(cfg: Config) -> int:
    server = ThreadingHTTPServer((cfg.serve_host, cfg.serve_port), build_handler(cfg))
    server.daemon_threads = True
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0

"""Credential-isolating gateway for OpenCode/OpenRouter rollouts.

The participant-controlled agent can call this service, but cannot read its
environment or filesystem.  The gateway accepts only the OpenRouter inference
surface, replaces any caller-supplied authorization header with the real key,
and never returns or logs that key.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import SplitResult, urlsplit


DEFAULT_UPSTREAM = "https://openrouter.ai"
DEFAULT_PORT = 8080
_ALLOWED_PATHS = frozenset(
    {
        "/api/v1/chat/completions",
        "/api/v1/models",
        "/api/v1/responses",
    }
)
_HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
    }
)
_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


def allowed_target(raw_target: str) -> str | None:
    """Return a safe origin-form target, or ``None`` when it is forbidden."""
    parsed = urlsplit(raw_target)
    if parsed.scheme or parsed.netloc or parsed.path not in _ALLOWED_PATHS:
        return None
    return parsed.path + (("?" + parsed.query) if parsed.query else "")


def forwarded_headers(headers: object, api_key: str) -> dict[str, str]:
    """Copy end-to-end headers while replacing all caller authentication."""
    items = getattr(headers, "items")
    result = {
        str(name): str(value)
        for name, value in items()
        if str(name).lower() not in _HOP_BY_HOP | {"authorization", "content-length", "host"}
    }
    result["Authorization"] = f"Bearer {api_key}"
    return result


def validated_response_header(name: str, value: str) -> tuple[str, str] | None:
    """Reject response headers that could alter the downstream header block."""
    if _HEADER_NAME.fullmatch(name) is None or "\r" in value or "\n" in value:
        return None
    return name, value


def _sanitize_tool_value(value: object) -> object:
    if isinstance(value, str):
        return value.replace('"$ref"', '"x-json-schema-ref"').replace(
            "'$ref'", "'x-json-schema-ref'"
        )
    if isinstance(value, list):
        return [_sanitize_tool_value(item) for item in value]
    if isinstance(value, dict):
        return {
            ("x-json-schema-ref" if key == "$ref" else key): _sanitize_tool_value(item)
            for key, item in value.items()
        }
    return value


def _sanitize_google_tool_outputs(payload: dict[str, object]) -> None:
    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, dict) and message.get("role") == "tool":
                message["content"] = _sanitize_tool_value(message.get("content"))
    inputs = payload.get("input")
    if isinstance(inputs, list):
        for item in inputs:
            if isinstance(item, dict) and item.get("type") == "function_call_output":
                item["output"] = _sanitize_tool_value(item.get("output"))


def routed_body(
    body: bytes | None,
    provider_only: tuple[str, ...],
    *,
    sanitize_google_tool_outputs: bool = False,
) -> bytes | None:
    """Force trusted OpenRouter provider routing without exposing it to agents."""
    if not body or (not provider_only and not sanitize_google_tool_outputs):
        return body
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return body
    if not isinstance(payload, dict):
        return body
    if sanitize_google_tool_outputs:
        _sanitize_google_tool_outputs(payload)
    provider = payload.get("provider")
    if not isinstance(provider, dict):
        provider = {}
    else:
        provider = dict(provider)
    provider["only"] = list(provider_only)
    payload["provider"] = provider
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


class ProviderGatewayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "integration-bench-provider-gateway"
    sys_version = ""

    def log_message(self, fmt: str, *args: object) -> None:
        # Request method/path/status are safe; headers and bodies are never logged.
        print(f"provider-gateway: {fmt % args}", file=sys.stderr, flush=True)

    def _json_error(self, status: int, message: str) -> None:
        body = json.dumps({"error": {"message": message}}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _forward(self) -> None:
        if self.path == "/_health" and self.command == "GET":
            body = b'{"status":"ok"}\n'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True
            return

        target = allowed_target(self.path)
        if target is None or self.command not in {"GET", "POST"}:
            self._json_error(403, "provider endpoint forbidden")
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json_error(400, "invalid content length")
            return
        if content_length < 0 or content_length > 32 * 1024 * 1024:
            self._json_error(413, "request body too large")
            return
        body = self.rfile.read(content_length) if content_length else None

        upstream: SplitResult = self.server.upstream  # type: ignore[attr-defined]
        api_key: str = self.server.api_key  # type: ignore[attr-defined]
        provider_only: tuple[str, ...] = self.server.provider_only  # type: ignore[attr-defined]
        sanitize_google_tool_outputs: bool = self.server.sanitize_google_tool_outputs  # type: ignore[attr-defined]
        body = routed_body(
            body,
            provider_only,
            sanitize_google_tool_outputs=sanitize_google_tool_outputs,
        )
        connection_class = (
            http.client.HTTPSConnection
            if upstream.scheme == "https"
            else http.client.HTTPConnection
        )
        kwargs: dict[str, object] = {"timeout": 1200}
        if upstream.scheme == "https":
            kwargs["context"] = ssl.create_default_context()
        connection = connection_class(upstream.hostname, upstream.port, **kwargs)
        upstream_target = (upstream.path.rstrip("/") + target) or target
        try:
            connection.request(
                self.command,
                upstream_target,
                body=body,
                headers=forwarded_headers(self.headers, api_key),
            )
            response = connection.getresponse()
            self.send_response(response.status, response.reason)
            for name, value in response.getheaders():
                header = validated_response_header(name, value)
                if header is not None and header[0].lower() not in _HOP_BY_HOP:
                    self.send_header(*header)
            self.send_header("Connection", "close")
            self.end_headers()
            while chunk := response.read(64 * 1024):
                self.wfile.write(chunk)
                self.wfile.flush()
        except (OSError, http.client.HTTPException, ssl.SSLError):
            if not getattr(self, "_headers_buffer", None):
                self._json_error(502, "provider gateway upstream failure")
        finally:
            self.close_connection = True
            connection.close()

    do_GET = _forward
    do_POST = _forward


class ProviderGatewayServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        upstream: str,
        api_key: str,
        provider_only: tuple[str, ...] = (),
        sanitize_google_tool_outputs: bool = False,
    ):
        parsed = urlsplit(upstream)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("IB_PROVIDER_UPSTREAM must be an absolute HTTP(S) URL")
        self.upstream = parsed
        self.api_key = api_key
        self.provider_only = provider_only
        self.sanitize_google_tool_outputs = sanitize_google_tool_outputs
        super().__init__(address, ProviderGatewayHandler)


def main() -> int:
    api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not api_key:
        print("provider-gateway: OPENROUTER_API_KEY is required", file=sys.stderr)
        return 2
    upstream = os.environ.get("IB_PROVIDER_UPSTREAM", DEFAULT_UPSTREAM).strip()
    provider_only = tuple(
        item.strip() for item in os.environ.get("IB_PROVIDER_ONLY", "").split(",") if item.strip()
    )
    sanitize_google_tool_outputs = os.environ.get(
        "IB_SANITIZE_GOOGLE_TOOL_OUTPUTS", ""
    ).strip() in {"1", "true", "yes"}
    port = int(os.environ.get("IB_PROVIDER_GATEWAY_PORT", str(DEFAULT_PORT)))
    server = ProviderGatewayServer(
        ("0.0.0.0", port),
        upstream=upstream,
        api_key=api_key,
        provider_only=provider_only,
        sanitize_google_tool_outputs=sanitize_google_tool_outputs,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

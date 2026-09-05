import http.client
import json
import threading
from email.message import Message
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from bench.provider_gateway import (
    ProviderGatewayServer,
    allowed_target,
    forwarded_headers,
    routed_body,
    validated_response_header,
)


def test_gateway_allows_only_inference_endpoints() -> None:
    assert allowed_target("/api/v1/chat/completions") == "/api/v1/chat/completions"
    assert allowed_target("/api/v1/models?foo=bar") == "/api/v1/models?foo=bar"
    assert allowed_target("/api/v1/auth/key") is None
    assert allowed_target("https://evil.invalid/api/v1/chat/completions") is None


def test_gateway_replaces_caller_auth_without_forwarding_hop_headers() -> None:
    headers = Message()
    headers["Authorization"] = "Bearer participant-visible-placeholder"
    headers["Proxy-Authorization"] = "bad"
    headers["Content-Type"] = "application/json"
    result = forwarded_headers(headers, "real-provider-secret")
    assert result == {
        "Content-Type": "application/json",
        "Authorization": "Bearer real-provider-secret",
    }


def test_gateway_rejects_response_header_splitting() -> None:
    assert validated_response_header("Content-Type", "application/json") == (
        "Content-Type",
        "application/json",
    )
    assert validated_response_header("Bad:Name", "value") is None
    assert validated_response_header("X-Test", "safe\r\nInjected: true") is None


def test_gateway_forces_trusted_provider_routing() -> None:
    body = routed_body(
        b'{"model":"google/gemini-3.7-flash","provider":{"sort":"price"}}',
        ("google-vertex",),
    )
    assert body is not None
    assert json.loads(body) == {
        "model": "google/gemini-3.7-flash",
        "provider": {"sort": "price", "only": ["google-vertex"]},
    }


def test_gateway_leaves_body_untouched_without_a_route() -> None:
    body = b'{"model":"meta/muse-spark-1.2"}'
    assert routed_body(body, ()) is body


def test_gateway_sanitizes_schema_refs_only_inside_google_tool_outputs() -> None:
    body = routed_body(
        json.dumps(
            {
                "messages": [
                    {"role": "user", "content": '{"$ref":"keep-user-content"}'},
                    {
                        "role": "tool",
                        "content": '{"$ref":"#/components/schemas/Error"}',
                    },
                ],
                "input": [
                    {
                        "type": "function_call_output",
                        "output": {"$ref": "#/components/schemas/Error"},
                    }
                ],
            }
        ).encode(),
        (),
        sanitize_google_tool_outputs=True,
    )
    assert body is not None
    payload = json.loads(body)
    assert payload["messages"][0]["content"] == '{"$ref":"keep-user-content"}'
    assert payload["messages"][1]["content"] == (
        '{"x-json-schema-ref":"#/components/schemas/Error"}'
    )
    assert payload["input"][0]["output"] == {"x-json-schema-ref": "#/components/schemas/Error"}


def test_gateway_forwards_inference_with_real_auth_only() -> None:
    observed: dict[str, object] = {}

    class UpstreamHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass

        def do_POST(self) -> None:
            size = int(self.headers.get("Content-Length", "0"))
            observed.update(
                path=self.path,
                authorization=self.headers.get("Authorization"),
                body=self.rfile.read(size),
            )
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamHandler)
    gateway = ProviderGatewayServer(
        ("127.0.0.1", 0),
        upstream=f"http://127.0.0.1:{upstream.server_port}",
        api_key="real-provider-secret",
    )
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True) for server in (upstream, gateway)
    ]
    for thread in threads:
        thread.start()
    try:
        client = http.client.HTTPConnection("127.0.0.1", gateway.server_port, timeout=5)
        client.request(
            "POST",
            "/api/v1/chat/completions",
            body=b'{"model":"test"}',
            headers={
                "Authorization": "Bearer participant-placeholder",
                "Content-Type": "application/json",
            },
        )
        response = client.getresponse()
        assert response.status == 200
        assert response.read() == b'{"ok":true}'
        assert observed == {
            "path": "/api/v1/chat/completions",
            "authorization": "Bearer real-provider-secret",
            "body": b'{"model":"test"}',
        }
    finally:
        gateway.shutdown()
        upstream.shutdown()
        gateway.server_close()
        upstream.server_close()

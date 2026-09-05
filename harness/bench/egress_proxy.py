"""Minimal fail-closed network gates for benchmark containers.

The default mode is an HTTPS CONNECT proxy used by model CLIs. It is not a
general web proxy: destinations must exactly match ``IB_EGRESS_ALLOWLIST``,
the port must be 443, and IP literals are rejected.

When ``IB_FORWARD_TARGET`` is set, the same small server becomes a fixed-target
TCP gateway. The harness uses that mode to publish the candidate app's webhook
port without attaching candidate code to an egress-capable Docker network.

Docker provides the actual bypass protection.  Contestant-controlled services
live on an internal network with no external route; this proxy is the sole
service attached to both that network and an egress-capable network.
"""

from __future__ import annotations

import ipaddress
import os
import selectors
import socket
import socketserver
import sys
from collections.abc import Iterable

LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 3128
MAX_HEADER_BYTES = 32 * 1024
HEADER_TIMEOUT_S = 10
CONNECT_TIMEOUT_S = 15
BUFFER_BYTES = 64 * 1024


def parse_allowlist(value: str) -> frozenset[str]:
    """Return normalized exact DNS names from a comma-separated value."""
    hosts: set[str] = set()
    for raw in value.split(","):
        host = raw.strip().rstrip(".").lower()
        if not host:
            continue
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValueError("egress allowlist entries must be DNS names, not IP addresses")
        if any(not label or len(label) > 63 for label in host.split(".")):
            raise ValueError(f"invalid egress allowlist hostname: {raw!r}")
        hosts.add(host)
    return frozenset(hosts)


def parse_connect_target(target: str) -> tuple[str, int]:
    """Parse a CONNECT authority and reject ambiguous or unsafe forms."""
    if target.count(":") != 1:
        raise ValueError("CONNECT target must be a DNS hostname and port")
    raw_host, raw_port = target.rsplit(":", 1)
    host = raw_host.strip().rstrip(".").lower()
    if not host or not raw_port.isdigit():
        raise ValueError("CONNECT target must include a numeric port")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("CONNECT targets may not be IP literals")
    if any(not label or len(label) > 63 for label in host.split(".")):
        raise ValueError("invalid CONNECT hostname")
    return host, int(raw_port)


def parse_forward_target(target: str) -> tuple[str, int]:
    """Parse the harness-owned fixed forwarding target."""
    host, port = parse_connect_target(target)
    if not 1 <= port <= 65535:
        raise ValueError("forward target port is out of range")
    return host, port


def target_allowed(host: str, port: int, allowlist: Iterable[str]) -> bool:
    """Only exact allowlisted provider names over TLS are reachable."""
    return port == 443 and host in allowlist


class _ProxyHandler(socketserver.BaseRequestHandler):
    server: "EgressProxyServer"

    def _reply(self, status: str) -> None:
        self.request.sendall(
            f"HTTP/1.1 {status}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n".encode("ascii")
        )

    def _read_headers(self) -> bytes:
        self.request.settimeout(HEADER_TIMEOUT_S)
        data = bytearray()
        while b"\r\n\r\n" not in data:
            chunk = self.request.recv(4096)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_HEADER_BYTES:
                raise ValueError("proxy request headers too large")
        return bytes(data)

    def handle(self) -> None:
        try:
            headers = self._read_headers()
            first_line = headers.split(b"\r\n", 1)[0].decode("ascii", "strict")
            method, target, _version = first_line.split(" ", 2)
        except (OSError, UnicodeError, ValueError):
            self._reply("400 Bad Request")
            return

        if method != "CONNECT":
            self._reply("403 Forbidden")
            return
        try:
            host, port = parse_connect_target(target)
        except ValueError:
            self._reply("403 Forbidden")
            return
        if not target_allowed(host, port, self.server.allowlist):
            self.server.audit("DENY", host, port)
            self._reply("403 Forbidden")
            return

        try:
            upstream = socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_S)
        except OSError:
            self.server.audit("ERROR", host, port)
            self._reply("502 Bad Gateway")
            return

        self.server.audit("ALLOW", host, port)
        try:
            self.request.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            relay_sockets(self.request, upstream)
        finally:
            upstream.close()


class _ForwardHandler(socketserver.BaseRequestHandler):
    server: "FixedForwardServer"

    def handle(self) -> None:
        try:
            upstream = socket.create_connection(self.server.target, timeout=CONNECT_TIMEOUT_S)
        except OSError:
            return
        try:
            relay_sockets(self.request, upstream)
        finally:
            upstream.close()


def relay_sockets(client: socket.socket, upstream: socket.socket) -> None:
    selector = selectors.DefaultSelector()
    selector.register(client, selectors.EVENT_READ, upstream)
    selector.register(upstream, selectors.EVENT_READ, client)
    try:
        while True:
            events = selector.select(timeout=60)
            if not events:
                continue
            for key, _mask in events:
                source: socket.socket = key.fileobj
                destination: socket.socket = key.data
                try:
                    data = source.recv(BUFFER_BYTES)
                except (BlockingIOError, InterruptedError):
                    continue
                if not data:
                    return
                destination.sendall(data)
    finally:
        selector.close()


class EgressProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], allowlist: frozenset[str]):
        self.allowlist = allowlist
        super().__init__(address, _ProxyHandler)

    @staticmethod
    def audit(action: str, host: str, port: int) -> None:
        print(f"{action} {host}:{port}", file=sys.stderr, flush=True)


class FixedForwardServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address: tuple[str, int], target: tuple[str, int]):
        self.target = target
        super().__init__(address, _ForwardHandler)


def main() -> int:
    try:
        listen_port = int(os.environ.get("IB_LISTEN_PORT", str(LISTEN_PORT)))
        if not 1 <= listen_port <= 65535:
            raise ValueError("listen port is out of range")
    except ValueError as exc:
        print(f"invalid IB_LISTEN_PORT: {exc}", file=sys.stderr)
        return 2
    forward = os.environ.get("IB_FORWARD_TARGET", "").strip()
    if forward:
        try:
            target = parse_forward_target(forward)
        except ValueError as exc:
            print(f"invalid IB_FORWARD_TARGET: {exc}", file=sys.stderr)
            return 2
        print(
            f"fixed gateway listening on {LISTEN_HOST}:{listen_port}; "
            f"target={target[0]}:{target[1]}",
            file=sys.stderr,
            flush=True,
        )
        with FixedForwardServer((LISTEN_HOST, listen_port), target) as server:
            server.serve_forever()
        return 0

    try:
        allowlist = parse_allowlist(os.environ.get("IB_EGRESS_ALLOWLIST", ""))
    except ValueError as exc:
        print(f"invalid IB_EGRESS_ALLOWLIST: {exc}", file=sys.stderr)
        return 2
    print(
        "egress proxy listening on "
        f"{LISTEN_HOST}:{listen_port}; allowed={','.join(sorted(allowlist)) or '<none>'}",
        file=sys.stderr,
        flush=True,
    )
    with EgressProxyServer((LISTEN_HOST, listen_port), allowlist) as server:
        server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

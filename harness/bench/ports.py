"""Free host-port allocation for compose port publishing overrides."""

from __future__ import annotations

import hashlib
import socket


def free_port() -> int:
    """Return an OS-assigned free TCP port on localhost.

    There is a narrow TOCTOU race between closing this socket and docker
    compose binding the same port, but it is the standard pragmatic approach
    (used by e.g. pytest-xdist, testcontainers) and acceptable for a local
    hermetic benchmark harness.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def stable_free_port(key: str, *, low: int = 20_000, high: int = 59_999) -> int:
    """Choose an available port from a key-stable, low-collision range.

    ``free_port()`` is appropriate when the caller binds immediately.  A
    compose-unit webhook port is different: it is selected at stack creation
    and rebound by several later ``compose run --service-ports`` calls.  Under
    parallel validation, the OS commonly hands two short-lived probes the same
    just-released ephemeral port, so one otherwise-correct task fails with
    ``port is already allocated``.  Starting each eval at a hash-derived port
    makes independent evals use independent search sequences while retaining an
    availability check for unrelated host services.
    """
    if low < 1024 or high > 65535 or low > high:
        raise ValueError("invalid stable port range")
    span = high - low + 1
    start = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big") % span
    for offset in range(span):
        port = low + ((start + offset) % span)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    raise RuntimeError(f"no free TCP port in {low}-{high}")

"""Shared fault-resilience layer for poll and writeback. See ``PROBLEM.md``."""

from __future__ import annotations

import time
from typing import Any, Callable

from placemint_summit.client import PlacemintClient

MAX_ATTEMPTS = 20
_BACKOFF_S = [0.2, 0.5, 1.0, 2.0, 4.0, 8.0]


def resilient_call(
    client: PlacemintClient,
    make_request: Callable[[], tuple[int, dict[str, Any], dict[str, str]]],
) -> tuple[int, dict[str, Any]]:
    """Call ``make_request``; retry the same logical request when appropriate.

    ``make_request`` must be safe to call repeatedly. Returns final
    ``(status, body)``; stops after ``MAX_ATTEMPTS``.
    """
    raise NotImplementedError

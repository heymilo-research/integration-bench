"""StaffLine writeback. See ``PROBLEM.md``."""

from __future__ import annotations

from typing import Any

from .client import StafflineClient
from .config import Config
from .writeback_requests import PENDING_WRITES

RPC_PATH = "/svc/do"


def process_writes(config: Config) -> list[dict[str, Any]]:
    """Push every pending write to StaffLine and return one result per write.

    See PROBLEM.md for the result shape.
    """
    raise NotImplementedError

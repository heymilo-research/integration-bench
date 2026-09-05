"""Robust readers for task outputs written to bind-mounted volumes.

On macOS Docker Desktop (VirtioFS), a file written inside a container can
take a moment to appear — or to finish syncing — on the host side. A direct
read immediately after `docker compose run` returns can therefore see a
missing or truncated file. Verifier scenarios should read declared outputs
through `read_json_output`, which polls until the file parses as JSON (a
truncated JSON document does not parse, so a successful parse implies a
complete file).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def read_json_output(
    path: Path | str,
    *,
    timeout_s: float = 15.0,
    poll_s: float = 0.25,
) -> Any | None:
    """Read and parse a JSON output file, tolerating bind-mount sync lag.

    Retries until the file exists and parses, up to timeout_s. Returns the
    parsed value, or None if the file never became readable — callers record
    that as a failed check rather than crashing the grade.
    """
    path = Path(path)
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            if time.monotonic() >= deadline:
                return None
            time.sleep(poll_s)

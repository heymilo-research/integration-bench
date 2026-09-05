"""CLI entry point.

    python -m fenmarsh_census
"""

from __future__ import annotations

import json
import sys

from fenmarsh_census.census import run_census
from fenmarsh_census.config import Config


def _entrypoint(argv: list[str] | None = None) -> int:
    cfg = Config.from_env()
    try:
        summary = run_census(cfg)
    except Exception as exc:  # noqa: BLE001 -- one failure surface for the runner
        print(f"roster census failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_entrypoint())

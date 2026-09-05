"""CLI entry point.

    python -m skill_index           # one weekly capability index
"""

from __future__ import annotations

import json
import sys

from skill_index.config import Config
from skill_index.index import run_index


def _entrypoint(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "build"
    cfg = Config.from_env()

    if command == "build":
        try:
            summary = run_index(cfg)
        except Exception as exc:  # noqa: BLE001 - the scheduler wants a code, not a trace
            print(f"index build failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    print(f"unknown command: {command!r} (expected 'build')", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_entrypoint())

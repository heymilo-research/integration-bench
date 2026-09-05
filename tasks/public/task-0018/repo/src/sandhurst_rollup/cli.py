"""CLI entry point.

    python -m sandhurst_rollup sync     # one nightly rollup pass
"""

from __future__ import annotations

import json
import sys

from sandhurst_rollup.config import Config
from sandhurst_rollup.rollup import run_rollup


def _entrypoint(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "sync"
    cfg = Config.from_env()

    if command == "sync":
        try:
            summary = run_rollup(cfg)
        except Exception as exc:  # noqa: BLE001 - the scheduler wants a code, not a trace
            print(f"rollup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(summary["counts"], indent=2, sort_keys=True))
        return 0

    print(f"unknown command: {command!r} (expected 'sync')", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_entrypoint())

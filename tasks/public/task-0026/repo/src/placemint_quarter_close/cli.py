"""CLI entry point.

    python -m placemint_quarter_close close-quarter
"""

from __future__ import annotations

import json
import sys

from placemint_quarter_close.close import run_quarter_close
from placemint_quarter_close.config import Config


def _entrypoint(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "close-quarter"
    cfg = Config.from_env()

    if command == "close-quarter":
        try:
            summary = run_quarter_close(cfg)
        except Exception as exc:  # noqa: BLE001 -- one failure surface for the runner
            print(f"quarter close failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    print(f"unknown command: {command!r} (expected 'close-quarter')", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_entrypoint())

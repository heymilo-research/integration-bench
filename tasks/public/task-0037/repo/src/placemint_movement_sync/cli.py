"""CLI entry point.

    python -m placemint_movement_sync apply-movements
"""

from __future__ import annotations

import json
import sys

from placemint_movement_sync.config import Config
from placemint_movement_sync.movements import run_movement_sync


def _entrypoint(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "apply-movements"
    cfg = Config.from_env()

    if command == "apply-movements":
        try:
            summary = run_movement_sync(cfg)
        except Exception as exc:  # noqa: BLE001 -- one failure surface for the runner
            print(f"movement sync failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    print(f"unknown command: {command!r} (expected 'apply-movements')", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_entrypoint())

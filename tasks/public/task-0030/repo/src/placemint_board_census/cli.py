"""CLI entry point.

    python -m placemint_board_census run-census
"""

from __future__ import annotations

import json
import sys

from placemint_board_census.census import run_board_census
from placemint_board_census.config import Config


def _entrypoint(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "run-census"
    cfg = Config.from_env()

    if command == "run-census":
        try:
            summary = run_board_census(cfg)
        except Exception as exc:  # noqa: BLE001 -- one failure surface for the runner
            print(f"board census failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    print(f"unknown command: {command!r} (expected 'run-census')", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_entrypoint())

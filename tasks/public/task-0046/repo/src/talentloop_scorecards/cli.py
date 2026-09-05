"""CLI entry point.

    python -m talentloop_scorecards deliver    # deliver the scorecard export
"""

from __future__ import annotations

import json
import sys

from talentloop_scorecards.config import Config
from talentloop_scorecards.deliver import run_delivery


def _entrypoint(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "deliver"
    cfg = Config.from_env()

    if command == "deliver":
        try:
            summary = run_delivery(cfg)
        except Exception as exc:  # noqa: BLE001 - the scheduler wants a nonzero exit
            print(f"delivery failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    print(f"unknown command: {command!r} (expected 'deliver')", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_entrypoint())

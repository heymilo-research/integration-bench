"""CLI entry point.

    python -m vaultd_cutover sync   # one change-feed cycle
"""

from __future__ import annotations

import json
import sys

from vaultd_cutover.config import Config
from vaultd_cutover.feed import run_cycle


def _entrypoint(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "sync"
    cfg = Config.from_env()

    if command == "sync":
        try:
            summary = run_cycle(cfg)
        except Exception as exc:  # noqa: BLE001 - the cycle reports, then fails
            print(f"cycle failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(
            {k: v for k, v in summary.items() if k != "changes"},
            indent=2, sort_keys=True))
        return 0

    print(f"unknown command: {command!r} (expected 'sync')", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_entrypoint())

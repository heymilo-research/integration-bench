"""CLI entry point.

    python -m pg_closure_migrate               # one cutover pass
"""

from __future__ import annotations

import json
import sys

from pg_closure_migrate.closure import run_migration
from pg_closure_migrate.config import Config


def _entrypoint(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "migrate"
    cfg = Config.from_env()

    if command == "migrate":
        try:
            summary = run_migration(cfg)
        except Exception as exc:  # noqa: BLE001 - the scheduler wants a code, not a trace
            print(f"cutover failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    print(f"unknown command: {command!r} (expected 'migrate')", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_entrypoint())

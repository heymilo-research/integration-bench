"""CLI entry point.

    python -m crewcall_signup_import import-signups
"""

from __future__ import annotations

import json
import sys

from crewcall_signup_import.config import Config
from crewcall_signup_import.reconcile import run_signup_import


def _entrypoint(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "import-signups"
    cfg = Config.from_env()

    if command == "import-signups":
        try:
            summary = run_signup_import(cfg)
        except Exception as exc:  # noqa: BLE001 -- one failure surface for the runner
            print(f"signup import failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    print(f"unknown command: {command!r} (expected 'import-signups')", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_entrypoint())

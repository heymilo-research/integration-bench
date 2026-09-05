"""CLI entry point.

    python -m placemint_contact_sweep sweep
"""

from __future__ import annotations

import json
import sys

from placemint_contact_sweep.config import Config
from placemint_contact_sweep.sweep import run_contact_sweep


def _entrypoint(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "sweep"
    cfg = Config.from_env()

    if command == "sweep":
        try:
            summary = run_contact_sweep(cfg)
        except Exception as exc:  # noqa: BLE001 -- one failure surface for the runner
            print(f"contact sweep failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    print(f"unknown command: {command!r} (expected 'sweep')", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_entrypoint())

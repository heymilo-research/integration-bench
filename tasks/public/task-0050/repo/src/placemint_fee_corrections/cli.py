"""CLI entry point.

    python -m placemint_fee_corrections apply-corrections
"""

from __future__ import annotations

import json
import sys

from placemint_fee_corrections.config import Config
from placemint_fee_corrections.corrections import run_corrections


def _entrypoint(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "apply-corrections"
    cfg = Config.from_env()

    if command == "apply-corrections":
        try:
            summary = run_corrections(cfg)
        except Exception as exc:  # noqa: BLE001 -- one failure surface for the runner
            print(f"corrections run failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    print(f"unknown command: {command!r} (expected 'apply-corrections')", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_entrypoint())

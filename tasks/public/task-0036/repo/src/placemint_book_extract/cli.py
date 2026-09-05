"""CLI entry point.

    python -m placemint_book_extract extract
"""

from __future__ import annotations

import json
import sys

from placemint_book_extract.config import Config
from placemint_book_extract.extract import run_book_extract


def _entrypoint(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "extract"
    cfg = Config.from_env()

    if command == "extract":
        try:
            summary = run_book_extract(cfg)
        except Exception as exc:  # noqa: BLE001 -- one failure surface for the runner
            print(f"book extract failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    print(f"unknown command: {command!r} (expected 'extract')", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_entrypoint())

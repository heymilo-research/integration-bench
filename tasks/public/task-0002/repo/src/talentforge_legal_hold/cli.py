"""CLI entry point.

    python -m talentforge_legal_hold export    # build output/legal_hold_export.json
"""

from __future__ import annotations

import json
import sys

from talentforge_legal_hold.config import Config
from talentforge_legal_hold.sweep import run_export


def _entrypoint(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "export"
    cfg = Config.from_env()

    if command == "export":
        try:
            payload = run_export(cfg)
        except Exception as exc:  # noqa: BLE001
            print(f"export failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(
            json.dumps(
                {
                    "roster_row_count": payload.get("roster_row_count"),
                    "custodian_count": payload.get("custodian_count"),
                    "note_count": payload.get("note_count"),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    print(f"unknown command: {command!r} (expected 'export')", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_entrypoint())

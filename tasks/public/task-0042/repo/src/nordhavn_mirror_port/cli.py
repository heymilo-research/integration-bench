"""CLI entry point.

    python -m nordhavn_mirror_port
"""

from __future__ import annotations

import json
import sys

from nordhavn_mirror_port.config import Config
from nordhavn_mirror_port.migrate import run_migration


def _entrypoint(argv: list[str] | None = None) -> int:
    cfg = Config.from_env()
    try:
        summary = run_migration(cfg)
    except Exception as exc:  # noqa: BLE001 -- one failure surface for the runner
        print(f"mirror migration failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_entrypoint())

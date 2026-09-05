"""CLI entry point.

    python -m marchfield_rota_sync
"""

from __future__ import annotations

import json
import sys

from marchfield_rota_sync.config import Config
from marchfield_rota_sync.sync import run_sync


def _entrypoint(argv: list[str] | None = None) -> int:
    cfg = Config.from_env()
    try:
        summary = run_sync(cfg)
    except Exception as exc:  # noqa: BLE001 -- one failure surface for the runner
        print(f"rota sync failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_entrypoint())

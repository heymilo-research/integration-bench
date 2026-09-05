"""CLI entry point.

    python -m ironvale_topup
"""

from __future__ import annotations

import json
import sys

from ironvale_topup.config import Config
from ironvale_topup.topup import run_topup


def _entrypoint(argv: list[str] | None = None) -> int:
    cfg = Config.from_env()
    try:
        summary = run_topup(cfg)
    except Exception as exc:  # noqa: BLE001 -- one failure surface for the runner
        print(f"crew top-up failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_entrypoint())

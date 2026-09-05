"""CLI entry point.

    python -m ellerby_note_mirror
"""

from __future__ import annotations

import json
import sys

from ellerby_note_mirror.config import Config
from ellerby_note_mirror.mirror import run_mirror


def _entrypoint(argv: list[str] | None = None) -> int:
    cfg = Config.from_env()
    try:
        summary = run_mirror(cfg)
    except Exception as exc:  # noqa: BLE001 -- one failure surface for the runner
        print(f"case note mirror failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_entrypoint())

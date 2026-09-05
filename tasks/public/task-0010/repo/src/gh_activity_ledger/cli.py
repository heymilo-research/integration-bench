"""CLI entry point.

    python -m gh_activity_ledger     # write both artifacts to OUTPUT_DIR
"""

from __future__ import annotations

import json
import sys

from gh_activity_ledger.client import GlobalHireClient
from gh_activity_ledger.config import Config
from gh_activity_ledger.ledger import build_ledger
from gh_activity_ledger.store import LedgerStore
from gh_activity_ledger.windows import read_windows


def _entrypoint(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] not in ("run", "ledger"):
        print(f"unknown command: {args[0]!r} (expected no argument, or 'run')", file=sys.stderr)
        return 2

    cfg = Config.from_env()
    client = GlobalHireClient(cfg)
    store = LedgerStore(cfg.output_dir)

    try:
        windows = read_windows()
        result = build_ledger(client, windows)
    except Exception as exc:  # noqa: BLE001 - scheduled batch job; report and fail
        print(f"ledger failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    summary = store.write_all(result, windows)
    print(json.dumps({"tenant": summary["tenant"],
                      "outside_windows": summary["outside_windows"],
                      "pages_fetched": client.pages_fetched},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_entrypoint())

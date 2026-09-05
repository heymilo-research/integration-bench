"""CLI entry point.

    python -m tf_event_cutover sync     # one nightly pass
    python -m tf_event_cutover dump     # print what the mirror currently holds

`sync` exits 0 on a completed pass and non-zero if the pass could not
complete, so the scheduler can tell "nothing changed" from "the run failed".
"""

from __future__ import annotations

import json
import sys

from tf_event_cutover.config import Config
from tf_event_cutover.store import Store
from tf_event_cutover.sync import run_sync


def _entrypoint(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "sync"
    cfg = Config.from_env()

    if command == "sync":
        try:
            summary = run_sync(cfg)
        except Exception as exc:  # a failed pass must not look like a clean one
            print(f"sync failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    if command == "dump":
        store = Store(cfg.output_dir)
        print(json.dumps(store.rows(), indent=2, sort_keys=True))
        return 0

    print(f"unknown command: {command!r} (expected 'sync' or 'dump')", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(_entrypoint())

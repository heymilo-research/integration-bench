"""CLI. See ``PROBLEM.md``."""

from __future__ import annotations

import argparse
import sys

from paygrade_sync.config import Config


def _cmd_sync(cfg: Config) -> int:
    from paygrade_sync import sync

    sync.run_sync(cfg)
    return 0


def _cmd_writeback(cfg: Config) -> int:
    from paygrade_sync import writeback

    writeback.run_writeback(cfg)
    return 0


def _cmd_dump(cfg: Config) -> int:
    from paygrade_sync.store import Store

    store = Store(cfg.output_dir)
    store.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="paygrade_sync")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync", help="poll employees/assignments + sweep tombstones")
    sub.add_parser("writeback", help="push the staged writes into Paygrade")
    sub.add_parser("dump", help="re-emit the canonical store")

    args = parser.parse_args(argv)
    cfg = Config.from_env()

    if args.command == "sync":
        return _cmd_sync(cfg)
    if args.command == "writeback":
        return _cmd_writeback(cfg)
    if args.command == "dump":
        return _cmd_dump(cfg)
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover
    return 2


def _entrypoint() -> None:
    sys.exit(main())


if __name__ == "__main__":
    _entrypoint()

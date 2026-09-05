"""CLI. See ``PROBLEM.md``."""

from __future__ import annotations

import argparse
import sys

from interviewly_relay.config import Config


def _cmd_sync(config: Config) -> int:
    from interviewly_relay.sync import sync

    sync(config)
    return 0


def _cmd_serve(config: Config) -> int:
    from interviewly_relay.webhooks import serve

    serve(config)
    return 0


def _cmd_dump(config: Config) -> int:
    from interviewly_relay.store import Store

    store = Store(config.output_dir)
    for table in ("interviews", "panelists", "feedback"):
        rows = store.load(table)
        store.write(table, rows)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="interviewly_relay")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync", help="run one polling pass (backfill or reconcile)")
    sub.add_parser("serve", help="run the webhook listener")
    sub.add_parser("dump", help="re-write the canonical store to OUTPUT_DIR")

    args = parser.parse_args(argv)
    config = Config.from_env()

    if args.command == "sync":
        return _cmd_sync(config)
    if args.command == "serve":
        return _cmd_serve(config)
    if args.command == "dump":
        return _cmd_dump(config)
    parser.error(f"unknown command {args.command!r}")
    return 2


def _entrypoint() -> None:
    sys.exit(main())


if __name__ == "__main__":
    sys.exit(main())

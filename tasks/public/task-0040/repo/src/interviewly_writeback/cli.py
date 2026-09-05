"""CLI. See ``PROBLEM.md``."""

from __future__ import annotations

import argparse
import sys

from interviewly_writeback.config import Config


def _cmd_sync(config: Config) -> int:
    from interviewly_writeback.sync import sync

    sync(config)
    return 0


def _cmd_push(config: Config) -> int:
    from interviewly_writeback.writeback import run_push

    run_push(config)
    return 0


def _cmd_serve(config: Config) -> int:
    from interviewly_writeback.webhooks import serve

    serve(config)
    return 0


def _cmd_dump(config: Config) -> int:
    from interviewly_writeback.store import Store
    from interviewly_writeback.writeback import WRITEBACKS_TABLE, write_push_result

    store = Store(config.output_dir)
    for table in ("interviews", "panelists", "feedback"):
        rows = store.load(table)
        store.write(table, rows)

    writebacks = store.load(WRITEBACKS_TABLE)
    results = [
        {"client_ref": source_id, **row["data"]}
        for source_id, row in writebacks.items()
    ]
    write_push_result(config.output_dir, results)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="interviewly_writeback")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync", help="poll-backfill interviews/panelists/feedback")
    sub.add_parser("push", help="push the staged reschedules")
    sub.add_parser("serve", help="run the webhook listener + reconciliation backstop")
    sub.add_parser("dump", help="re-write OUTPUT_DIR from the current canonical store")

    args = parser.parse_args(argv)
    config = Config.from_env()

    if args.command == "sync":
        return _cmd_sync(config)
    if args.command == "push":
        return _cmd_push(config)
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

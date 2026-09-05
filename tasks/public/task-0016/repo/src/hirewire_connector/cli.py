"""CLI. See ``PROBLEM.md``."""

from __future__ import annotations

import argparse
import sys

from hirewire_connector.config import Config


def _cmd_push(cfg: Config) -> int:
    from hirewire_connector import sync

    sync.run_push(cfg)
    return 0


def _cmd_poll(cfg: Config) -> int:
    from hirewire_connector import sync

    sync.run_poll(cfg)
    return 0


def _cmd_dump(cfg: Config) -> int:
    from hirewire_connector.store import Store

    # Re-emit the canonical store from disk (no vendor traffic).
    store = Store(cfg.output_dir)
    store.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hirewire_connector")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("push", help="push the staged events into HireWire")
    sub.add_parser("poll", help="incrementally poll candidates from HireWire")
    sub.add_parser("dump", help="re-emit the canonical candidate store")

    args = parser.parse_args(argv)
    cfg = Config.from_env()

    if args.command == "push":
        return _cmd_push(cfg)
    if args.command == "poll":
        return _cmd_poll(cfg)
    if args.command == "dump":
        return _cmd_dump(cfg)
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover
    return 2


def _entrypoint() -> None:
    sys.exit(main())


if __name__ == "__main__":
    _entrypoint()

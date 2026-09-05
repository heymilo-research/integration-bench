"""CLI. See ``PROBLEM.md``."""

from __future__ import annotations

import argparse
import sys

from crewcall_sync.config import Config
from crewcall_sync.store import Store


def _cmd_sync(cfg: Config) -> int:
    from crewcall_sync import sync

    store = Store(cfg.output_dir)
    sync.run_sync(cfg, store)
    store.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="crewcall_sync")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("sync", help="run one full dedupe-and-converge snapshot pass")

    args = parser.parse_args(argv)
    cfg = Config.from_env()

    if args.command == "sync":
        rc = _cmd_sync(cfg)
    else:  # pragma: no cover - argparse enforces choices
        parser.error(f"unknown command {args.command!r}")
        rc = 2
    return rc


def _entrypoint() -> None:
    sys.exit(main())


if __name__ == "__main__":
    _entrypoint()

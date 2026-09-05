"""CLI. See ``PROBLEM.md``."""

from __future__ import annotations

import argparse
import json

from .config import Config


def _cmd_sync(config: Config) -> int:
    from .sync import sync

    sync(config)
    return 0


def _cmd_writeback(config: Config) -> int:
    from .writeback import process_writes

    results = process_writes(config)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    out = config.output_dir / "writeback.json"
    out.write_text(json.dumps(results, indent=2, sort_keys=False), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="staffline_fullsync")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("sync", help="run one full sync pass (candidates, applications, deletes)")
    sub.add_parser("writeback", help="push the pending write batch and record results")

    args = parser.parse_args(argv)
    config = Config.from_env()

    if args.command == "sync":
        return _cmd_sync(config)
    if args.command == "writeback":
        return _cmd_writeback(config)
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""CLI. See ``PROBLEM.md``."""

from __future__ import annotations

import argparse
import sys

from staffline_bulk import store
from staffline_bulk.config import Config


def _cmd_push(cfg: Config) -> int:
    from staffline_bulk import sync

    conn = store.connect(cfg.database_url)
    try:
        store.ensure_schema(conn)
        sync.run_push(cfg, conn)
    finally:
        conn.close()
    return 0


def _cmd_dump(cfg: Config) -> int:
    from staffline_bulk import sync

    conn = store.connect(cfg.database_url)
    try:
        store.ensure_schema(conn)
        rows = store.all_results(conn)
    finally:
        conn.close()
    sync.write_result(cfg.output_dir, rows)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="staffline_bulk")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("push", help="import the staged batch into StaffLine")
    sub.add_parser("dump", help="re-write the recorded outcome from the durable store")

    args = parser.parse_args(argv)
    cfg = Config.from_env()

    if args.command == "push":
        return _cmd_push(cfg)
    if args.command == "dump":
        return _cmd_dump(cfg)
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover
    return 2


def _entrypoint() -> None:
    sys.exit(main())


if __name__ == "__main__":
    _entrypoint()

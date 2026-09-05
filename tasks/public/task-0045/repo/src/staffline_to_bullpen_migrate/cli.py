"""CLI. See ``PROBLEM.md``."""

from __future__ import annotations

import argparse
import json

from staffline_to_bullpen_migrate.config import Config


def _cmd_baseline(config: Config) -> int:
    from staffline_to_bullpen_migrate.migrate import run_baseline

    run_baseline(config)
    return 0


def _cmd_migrate(config: Config) -> int:
    from staffline_to_bullpen_migrate.migrate import run_migrate

    run_migrate(config)
    return 0


def _cmd_writeback(config: Config) -> int:
    from staffline_to_bullpen_migrate.writeback import process_writes

    results = process_writes(config)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    out = config.output_dir / "writeback_result.json"
    out.write_text(json.dumps(results, indent=2, sort_keys=False), encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="staffline_to_bullpen_migrate")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("baseline", help="read StaffLine one last time (active + tombstones)")
    sub.add_parser("migrate", help="cutover backfill from Bullpen, union in legacy tombstones")
    sub.add_parser("writeback", help="push the pending write batch to Bullpen only")

    args = parser.parse_args(argv)
    config = Config.from_env()

    if args.command == "baseline":
        return _cmd_baseline(config)
    if args.command == "migrate":
        return _cmd_migrate(config)
    if args.command == "writeback":
        return _cmd_writeback(config)
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

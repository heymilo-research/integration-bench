"""CLI entrypoints: ``merge`` and ``correct``. See PROBLEM.md."""

from __future__ import annotations

import argparse

from staffline_placemint_merge.config import Config
from staffline_placemint_merge.store import read_json, write_roster


def _cmd_merge(config: Config) -> int:
    from staffline_placemint_merge.merge import run_merge

    rows = run_merge(config)
    write_roster(config.output_dir / "roster.json", rows)
    return 0


def _cmd_correct(config: Config) -> int:
    from staffline_placemint_merge.correct import run_correct

    roster = read_json(config.output_dir / "roster.json", [])
    run_correct(config, roster)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="staffline_placemint_merge")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("merge", help="backfill + join + precedence -> output/roster.json")
    sub.add_parser("correct", help="push corrective notes to StaffLine -> output/corrections.json")

    args = parser.parse_args(argv)
    config = Config.from_env()

    if args.command == "merge":
        return _cmd_merge(config)
    if args.command == "correct":
        return _cmd_correct(config)
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

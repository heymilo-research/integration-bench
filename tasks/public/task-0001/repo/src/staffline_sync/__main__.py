"""CLI entry point (COMPLETE): `python -m staffline_sync [--incremental]`."""

from __future__ import annotations

import argparse

from staffline_sync.config import Config
from staffline_sync.sync import run


def main() -> None:
    parser = argparse.ArgumentParser(prog="staffline_sync")
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="catch up on changes since the last successful pass, instead of a full back-fill",
    )
    args = parser.parse_args()

    config = Config.from_env()
    run(config, incremental=args.incremental)


if __name__ == "__main__":
    main()

"""CLI. See ``PROBLEM.md``."""

from __future__ import annotations

import argparse

from .config import Config
from .sync import sync


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vettly_sync")
    parser.add_argument(
        "--incremental",
        action="store_true",
        help="run an incremental catch-up pass instead of a full back-fill",
    )
    args = parser.parse_args(argv)

    config = Config.from_env()
    sync(config, incremental=args.incremental)
    return 0


def _entrypoint() -> None:
    raise SystemExit(main())


if __name__ == "__main__":
    raise SystemExit(main())

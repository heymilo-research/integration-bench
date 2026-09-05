from __future__ import annotations

import argparse
import sys

from hirewire_corrections.config import Config


def _cmd_correct(cfg: Config) -> int:
    from hirewire_corrections import corrections

    corrections.run_corrections(cfg)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hirewire_corrections")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("correct", help="run one correction-backlog pass")

    args = parser.parse_args(argv)
    cfg = Config.from_env()

    if args.command == "correct":
        return _cmd_correct(cfg)
    parser.error(f"unknown command {args.command!r}")  # pragma: no cover
    return 2


def _entrypoint() -> None:
    sys.exit(main())


if __name__ == "__main__":
    _entrypoint()

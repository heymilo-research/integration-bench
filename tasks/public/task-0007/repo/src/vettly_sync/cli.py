"""CLI. See ``PROBLEM.md``."""

from __future__ import annotations

import argparse
import json
import sys

from vettly_sync import store
from vettly_sync.config import Config

ENTITIES = ("subjects", "checks", "reports")


def _cmd_sync(cfg: Config) -> int:
    from vettly_sync import sync

    conn = store.connect(cfg.database_url)
    try:
        store.ensure_schema(conn)
        sync.run_sync(cfg, conn)
    finally:
        conn.close()
    return 0


def _cmd_dump(cfg: Config) -> int:
    conn = store.connect(cfg.database_url)
    try:
        store.ensure_schema(conn)
        rows_by_entity = {entity: store.all_rows(conn, entity) for entity in ENTITIES}
    finally:
        conn.close()
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    for entity, rows in rows_by_entity.items():
        out = cfg.output_dir / f"{entity}.json"
        with out.open("w", encoding="utf-8") as fh:
            json.dump(rows, fh, indent=2, sort_keys=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vettly-sync")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("sync", help="run one sync pass (backfill or incremental, per entity)")
    sub.add_parser("dump", help="write the canonical store to OUTPUT_DIR")

    args = parser.parse_args(argv)
    cfg = Config.from_env()

    if args.command == "sync":
        rc = _cmd_sync(cfg)
    elif args.command == "dump":
        rc = _cmd_dump(cfg)
    else:  # pragma: no cover - argparse enforces choices
        parser.error(f"unknown command {args.command!r}")
        rc = 2
    return rc


def _entrypoint() -> None:
    sys.exit(main())


if __name__ == "__main__":
    _entrypoint()

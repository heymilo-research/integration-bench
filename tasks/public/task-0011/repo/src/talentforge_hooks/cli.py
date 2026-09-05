"""CLI. See ``PROBLEM.md``."""

from __future__ import annotations

import argparse
import json
import sys

from talentforge_hooks import store
from talentforge_hooks.config import Config


def _cmd_backfill(cfg: Config) -> int:
    from talentforge_hooks import sync

    conn = store.connect(cfg.database_url)
    try:
        store.ensure_schema(conn)
        sync.run_backfill(cfg, conn)
    finally:
        conn.close()
    return 0


def _cmd_serve(cfg: Config, args: argparse.Namespace) -> int:
    from talentforge_hooks import webhooks

    conn = store.connect(cfg.database_url)
    try:
        store.ensure_schema(conn)
        webhooks.serve(
            cfg,
            conn,
            max_events=args.max_events,
            idle_timeout=args.idle_timeout,
            max_runtime=args.max_runtime,
        )
    finally:
        conn.close()
    return 0


def _cmd_push(cfg: Config) -> int:
    from talentforge_hooks import push

    push.run_push(cfg)
    return 0


def _cmd_dump(cfg: Config) -> int:
    conn = store.connect(cfg.database_url)
    try:
        store.ensure_schema(conn)
        candidates = store.all_rows(conn, "candidate")
        applications = store.all_rows(conn, "application")
    finally:
        conn.close()
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    with (cfg.output_dir / "candidates.json").open("w", encoding="utf-8") as fh:
        json.dump(candidates, fh, indent=2, sort_keys=True)
    with (cfg.output_dir / "applications.json").open("w", encoding="utf-8") as fh:
        json.dump(applications, fh, indent=2, sort_keys=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="talentforge_hooks")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("backfill", help="one-time full backfill of candidates + applications")

    p_serve = sub.add_parser("serve", help="run the webhook listener")
    p_serve.add_argument("--max-events", type=int, default=None,
                         help="exit after applying this many distinct events")
    p_serve.add_argument("--idle-timeout", type=float, default=None,
                         help="exit after this many seconds with no delivery")
    p_serve.add_argument("--max-runtime", type=float, default=None,
                         help="hard ceiling on total serve runtime in seconds")

    sub.add_parser("push", help="drain the staged writeback batch into TalentForge")
    sub.add_parser("dump", help="write the canonical stores to OUTPUT_DIR")

    args = parser.parse_args(argv)
    cfg = Config.from_env()

    if args.command == "backfill":
        rc = _cmd_backfill(cfg)
    elif args.command == "serve":
        rc = _cmd_serve(cfg, args)
    elif args.command == "push":
        rc = _cmd_push(cfg)
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

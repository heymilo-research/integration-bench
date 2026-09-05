"""CLI. See ``PROBLEM.md``."""

from __future__ import annotations

import sys

from globalhire_sync import store, sync
from globalhire_sync.client import GlobalHireClient
from globalhire_sync.config import Config

_COLLECTIONS = (
    ("candidates", sync.fetch_candidates),
    ("placements", sync.fetch_placements),
    ("agencies", sync.fetch_agencies),
)


def run(cfg: Config) -> int:
    client = GlobalHireClient(cfg)
    for name, fetch in _COLLECTIONS:
        rows = fetch(client)
        store.write_rows(cfg.output_dir / f"{name}.json", rows)
    return 0


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv:
        print(f"globalhire-sync: unexpected argument(s): {argv}", file=sys.stderr)
        return 2
    cfg = Config.from_env()
    return run(cfg)


def _entrypoint() -> None:
    sys.exit(main())


if __name__ == "__main__":
    _entrypoint()

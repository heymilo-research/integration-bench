"""Entry point.

One invocation is one cycle. The cycle number is kept in the state document, so
the first invocation is cycle 1, the next is cycle 2, and so on.
"""

from __future__ import annotations

import sys
import traceback

from northgate_placement_sync import reconcile
from northgate_placement_sync.clients import PlacemintClient, RecruitOSClient
from northgate_placement_sync.config import Config
from northgate_placement_sync.crosswalk import read_links
from northgate_placement_sync.report import write_artifacts
from northgate_placement_sync.statestore import StateStore


def _entrypoint(argv: list[str] | None = None) -> int:
    _argv = list(sys.argv[1:] if argv is None else argv)
    cfg = Config.from_env()
    store = StateStore(cfg.state_dir)
    state = store.load()

    try:
        links = read_links(cfg.crosswalk_file)
    except OSError as exc:
        print(f"crosswalk unreadable: {exc}", file=sys.stderr)
        return 2

    recruitos = RecruitOSClient(cfg)
    placemint = PlacemintClient(cfg)

    try:
        result, next_state = reconcile.run_cycle(
            recruitos=recruitos,
            placemint=placemint,
            links=links,
            state=state,
        )
    except Exception:  # noqa: BLE001 - a failed cycle must not exit 0
        traceback.print_exc()
        return 1

    write_artifacts(cfg.output_dir, result)
    store.save(next_state)
    print(
        f"cycle {result.cycle}: {len(result.rows)} link(s), {len(result.writes)} write(s)",
        file=sys.stderr,
    )
    return 0

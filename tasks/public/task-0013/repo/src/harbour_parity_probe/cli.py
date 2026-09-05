"""Entry point.

    python -m harbour_parity_probe serve    run the webhook listener
    python -m harbour_parity_probe sync     run one nightly pass (the default)
"""

from __future__ import annotations

import sys
import traceback

from harbour_parity_probe import listener, parity
from harbour_parity_probe.client import RecruitOSClient
from harbour_parity_probe.config import Config
from harbour_parity_probe.report import write_artifacts
from harbour_parity_probe.snapshot import read_snapshot


def _entrypoint(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "sync"
    cfg = Config.from_env()

    if command == "serve":
        return listener.serve(cfg)

    if command != "sync":
        print(f"unknown command {command!r}; expected 'sync' or 'serve'", file=sys.stderr)
        return 2

    try:
        snapshot = read_snapshot(cfg.snapshot_file)
    except (OSError, ValueError) as exc:
        print(f"warehouse snapshot unreadable: {exc}", file=sys.stderr)
        return 2

    client = RecruitOSClient(cfg)
    try:
        result = parity.run_parity(client=client, snapshot=snapshot)
    except Exception:  # noqa: BLE001 - a failed pass must not exit 0
        traceback.print_exc()
        return 1

    result.events = listener.read_events(cfg.state_dir)
    write_artifacts(cfg.output_dir, result)
    print(
        f"parity pass: {len(result.rows)} divergence(s), "
        f"{client.requests_made} request(s)",
        file=sys.stderr,
    )
    return 0

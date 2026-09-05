"""CLI. See ``PROBLEM.md``."""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import time

from placemint_summit import poll, writeback
from placemint_summit.client import PlacemintClient
from placemint_summit.config import Config
from placemint_summit.store import Store, canonical_rows, write_json
from placemint_summit.webhooks import start_listener


def _client(cfg: Config) -> PlacemintClient:
    return PlacemintClient(cfg.vendor_base_url, cfg.client_id, cfg.client_secret)


def _store(cfg: Config) -> Store:
    return Store(cfg.state_path)


class _PollThread(threading.Thread):
    """Background poll-reconciliation loop on ``POLL_INTERVAL_S`` cadence.

    Swallows transient pass failures and retries on the next tick.
    """

    def __init__(self, cfg: Config, client: PlacemintClient, store: Store) -> None:
        super().__init__(name="pm-poll-reconcile", daemon=True)
        self.cfg = cfg
        self.client = client
        self.store = store
        self._stop = threading.Event()

    def stop(self) -> None:
        self._stop.set()

    def _pass_once(self) -> None:
        # First pass: full backfill if the store is empty (fresh boot),
        # otherwise a normal reconcile from whatever watermark we already
        # have (a resumed/recreated container should not re-walk the whole
        # dataset every time).
        if not self.store.all_rows("placements") and not self.store.all_rows("clients"):
            poll.run_full_backfill(self.client, self.store)
        else:
            poll.run_reconcile(self.client, self.store)
        self.store.save()

    def run(self) -> None:
        try:
            self._pass_once()
        except Exception as exc:  # noqa: BLE001 - see class docstring
            print(f"pm-poll-reconcile: pass failed, will retry next cycle: {exc!r}", file=sys.stderr)

        while not self._stop.wait(self.cfg.poll_interval_s):
            try:
                self._pass_once()
            except Exception as exc:  # noqa: BLE001 - see class docstring
                print(f"pm-poll-reconcile: pass failed, will retry next cycle: {exc!r}", file=sys.stderr)


def _cmd_serve(cfg: Config, max_runtime: float | None) -> int:
    client = _client(cfg)
    store = _store(cfg)

    poll_thread = _PollThread(cfg, client, store)
    poll_thread.start()

    httpd, _applier = start_listener(cfg, client, store)

    stop_requested = threading.Event()

    def _handle_signal(_sig, _frame):
        stop_requested.set()

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    start = time.monotonic()
    try:
        while not stop_requested.is_set():
            if max_runtime is not None and (time.monotonic() - start) >= max_runtime:
                break
            time.sleep(0.2)
    finally:
        poll_thread.stop()
        httpd.shutdown()
        httpd.server_close()
        store.save()
    return 0


def _cmd_writeback(cfg: Config) -> int:
    client = _client(cfg)
    results = writeback.push_writes(client, cfg.pending_writes_path)
    results = sorted(results, key=lambda r: r["client_ref"])
    write_json(cfg.output_dir / "writeback_result.json", {"writes": results})
    return 0


def _cmd_dump(cfg: Config) -> int:
    store = _store(cfg)
    rows = canonical_rows(store.all_rows("placements"))
    write_json(cfg.output_dir / "placements.json", rows)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="placemint_summit")
    sub = parser.add_subparsers(dest="command", required=True)

    p_serve = sub.add_parser("serve", help="run the webhook listener + poll-reconciliation loop")
    p_serve.add_argument("--max-runtime", type=float, default=None,
                          help="hard ceiling on total serve runtime in seconds")

    sub.add_parser("writeback", help="drain input/pending_writes.json")
    sub.add_parser("dump", help="write the canonical placement store to OUTPUT_DIR")

    args = parser.parse_args(argv)
    cfg = Config.from_env()

    if args.command == "serve":
        rc = _cmd_serve(cfg, args.max_runtime)
    elif args.command == "writeback":
        rc = _cmd_writeback(cfg)
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

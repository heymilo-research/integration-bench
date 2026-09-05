from __future__ import annotations

from globalhire_mobility.client import GlobalHireClient
from globalhire_mobility.config import Config
from globalhire_mobility.import_file import logical_actions, read_actions
from globalhire_mobility.reconcile import reconcile
from globalhire_mobility.report import write_report


def run() -> None:
    config = Config.from_env()
    actions = read_actions(config.input_file)
    client = GlobalHireClient(config.base_url, config.api_key)
    cases = reconcile(client, logical_actions(actions))
    write_report(config.output_dir, len(actions), cases)


def entrypoint() -> int:
    try:
        run()
        return 0
    except Exception as exc:
        print(f"mobility repair failed: {type(exc).__name__}: {exc}")
        return 1

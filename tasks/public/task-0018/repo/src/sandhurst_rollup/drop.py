"""The Reporting Mart drop reader.

RecruitOS's Reporting Mart wrote one file per night into ``MART_DROP_DIR``,
named ``rollup-YYYY-MM-DD.csv``, already carrying every column Finance's
loader wants. This module finds the most recent one and parses it.

It is the source this pass has read since 2022.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from sandhurst_rollup.config import Config

DROP_GLOB = "rollup-*.csv"


class NoDropTonight(RuntimeError):
    """No mart drop is present in the drop directory."""


def latest_drop_path(cfg: Config) -> Path:
    drops = sorted(Path(cfg.mart_drop_dir).glob(DROP_GLOB))
    if not drops:
        raise NoDropTonight(
            f"no {DROP_GLOB} in {cfg.mart_drop_dir} -- the Reporting Mart has not "
            "delivered a file for tonight"
        )
    return drops[-1]


def read_latest_drop(cfg: Config) -> list[dict[str, Any]]:
    """Every line of tonight's mart drop, in file order."""
    path = latest_drop_path(cfg)
    with path.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]

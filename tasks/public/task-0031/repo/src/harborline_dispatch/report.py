"""Output writers for the morning board.

Two artifacts. Shape only -- every value below is a placeholder and none of
them describes any row of any real roster, gig board or tenant.

``availability_board.csv`` is one row per roster record, in the order it is
handed to :meth:`BoardWriter.write`::

    worker_id,worker_status,availability,blocked_by,blocking_gig_id
    wkr_0000,<status>,<availability>,,
    wkr_0000,<status>,<availability>,<reason>,gig_0000

``windows.json`` is the per-gig picking lists plus the board's own totals::

    {"generated_windows": [
       {"gig_id": "gig_0000",
        "gig_status": "<status>",
        "pay_rate_cents": 0,
        "eligible_count": 0,
        "eligible_worker_ids": ["wkr_0000", "..."]},
       ...],
     "totals": {"roster_rows": 0, "offerable": 0, "committed": 0,
                "not_available": 0, "off_roster": 0}}

``eligible_worker_ids`` is written sorted. ``generated_windows`` is written in
the order it is handed over -- that order is part of the contract, so it is the
caller's to decide. ``totals`` is derived from the board rows, so it cannot
disagree with the CSV.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

BOARD_COLUMNS = (
    "worker_id",
    "worker_status",
    "availability",
    "blocked_by",
    "blocking_gig_id",
)

AVAILABILITY_VALUES = ("offerable", "committed", "not_available", "off_roster")


class BoardWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def write(self, board_rows: list[dict[str, Any]],
              windows: list[dict[str, Any]]) -> dict[str, Any]:
        with (self.output_dir / "availability_board.csv").open(
            "w", encoding="utf-8", newline=""
        ) as fh:
            writer = csv.DictWriter(fh, fieldnames=list(BOARD_COLUMNS))
            writer.writeheader()
            for row in board_rows:
                writer.writerow({c: row.get(c, "") or "" for c in BOARD_COLUMNS})

        totals = {"roster_rows": len(board_rows)}
        for value in AVAILABILITY_VALUES:
            totals[value] = sum(
                1 for row in board_rows if row.get("availability") == value
            )

        payload = {
            "generated_windows": [
                {
                    "gig_id": window.get("gig_id"),
                    "gig_status": window.get("gig_status"),
                    "pay_rate_cents": window.get("pay_rate_cents"),
                    "eligible_count": len(window.get("eligible_worker_ids") or []),
                    "eligible_worker_ids": sorted(
                        str(w) for w in (window.get("eligible_worker_ids") or [])
                    ),
                }
                for window in windows
            ],
            "totals": totals,
        }
        (self.output_dir / "windows.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8"
        )
        return payload

"""Reader for Compliance's audit calendar.

The calendar sits at ``input/ledger_windows.csv``, relative to the package
root. Header row, one row per period, in the order Compliance listed them::

    window_id,starts_at,ends_at
    W0,2026-01-01T00:00:00Z,2026-01-01T00:00:00Z

``starts_at`` and ``ends_at`` are UTC ISO-8601 instants. A period is half-open:
it runs from ``starts_at`` inclusive to ``ends_at`` exclusive. Rows are handed
back in file order and are never reordered here -- the ledger has to line up
with the calendar Compliance is holding.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parents[2] / "input" / "ledger_windows.csv"

COLUMNS = ["window_id", "starts_at", "ends_at"]


def parse_instant(text: str) -> datetime:
    """A UTC ISO-8601 instant from the calendar, as an aware datetime."""
    value = str(text).strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class Window:
    window_id: str
    starts_at: str
    ends_at: str

    @property
    def start(self) -> datetime:
        return parse_instant(self.starts_at)

    @property
    def end(self) -> datetime:
        return parse_instant(self.ends_at)


def read_windows(path: Path | None = None) -> list[Window]:
    source = Path(path) if path is not None else CSV_PATH
    with source.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError(f"{source} is missing column(s): {missing}")
        windows = [
            Window(
                window_id=(row.get("window_id") or "").strip(),
                starts_at=(row.get("starts_at") or "").strip(),
                ends_at=(row.get("ends_at") or "").strip(),
            )
            for row in reader
            if (row.get("window_id") or "").strip()
        ]
    if not windows:
        raise ValueError(f"{source} lists no periods")
    return windows

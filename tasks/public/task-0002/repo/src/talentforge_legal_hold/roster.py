"""The legal-hold roster file.

``input/legal_hold_roster.csv`` is a plain comma-delimited file with a header
row::

    matter_ref,custodian_email
    XX-0000,someone@example.invalid

Emails are compared case-insensitively and whitespace-trimmed; that
normalisation happens here so the rest of the codebase never has to think
about it. Row order is the order counsel sent them and is not significant.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RosterRow:
    matter_ref: str
    email: str


def read_roster(path: Path) -> list[RosterRow]:
    with Path(path).open(newline="", encoding="utf-8") as fh:
        return [
            RosterRow(
                matter_ref=row["matter_ref"].strip(),
                email=row["custodian_email"].strip().lower(),
            )
            for row in csv.DictReader(fh)
            if (row.get("custodian_email") or "").strip()
        ]

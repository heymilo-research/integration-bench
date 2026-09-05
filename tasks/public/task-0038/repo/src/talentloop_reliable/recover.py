"""Recovery pass over undelivered events. See ``PROBLEM.md``."""

from __future__ import annotations

import sqlite3

from talentloop_reliable.client import TalentLoopClient  # noqa: F401
from talentloop_reliable.config import Config
from talentloop_reliable.sync import apply_application, apply_candidate, mark_deleted  # noqa: F401


def run_recover(cfg: Config, conn: sqlite3.Connection, client: TalentLoopClient | None = None) -> None:
    """Run one recovery pass over undelivered events."""
    raise NotImplementedError

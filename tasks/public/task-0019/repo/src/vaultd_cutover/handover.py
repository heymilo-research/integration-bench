"""vaultd's handover state file.

``$INPUT_DIR/vaultd_state.json`` is the broker's own bookkeeping for this feed,
copied out of ``/var/lib/vaultd/state/`` before the broker was switched off::

    {"broker": "vaultd",
     "feed": "<name>",
     "last_delivered_cursor": "<whatever vaultd wrote>",
     "cycles_delivered": 0}

``last_delivered_cursor`` is returned exactly as it appears in the file. What
that value means, and what has to be sent to Vettly to resume from it, is the
cycle's problem and not this reader's.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

STATE_FILENAME = "vaultd_state.json"


@dataclass(frozen=True)
class HandoverState:
    feed: str
    last_delivered_cursor: str
    cycles_delivered: int


def read_handover(input_dir: Path) -> HandoverState:
    raw = json.loads((Path(input_dir) / STATE_FILENAME).read_text(encoding="utf-8"))
    return HandoverState(
        feed=str(raw.get("feed") or ""),
        last_delivered_cursor=str(raw.get("last_delivered_cursor") or ""),
        cycles_delivered=int(raw.get("cycles_delivered") or 0),
    )

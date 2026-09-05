"""crawl_start — the cheap floor: a clean, uninterrupted full backfill.

CHECKPOINT=0, no faults, no interruption. Establishes that the connector
crawls all three collections correctly end to end against a quiet sandbox —
deliberately reachable by any working connector, including the starter's
(whose bug only fires on a cursor expiry, which cannot happen here: an
uninterrupted crawl consumes each cursor on the very next request to that
collection, well inside VT_CURSOR_TTL_REQS=5).

Because this rung is free by design it is scored as ONE check, not eight: "a
clean backfill lands the whole answer key". Splitting it per-entity, adding an
exit-code check alongside a store-contents check that cannot pass without a
successful exit, and adding per-entity no-duplicate checks that exact fixture
equality already implies, only multiplied the credit a submission banks for
clearing a rung that asks nothing of the ticket (measured 2026-08-01: 8 of 72
checks).

L1 : app exits 0 AND all three collections match the checkpoint-0 fixtures.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _scenario_util import (  # noqa: E402
    ENTITIES,
    dump_store,
    fixtures_for,
    recreate_vendor,
    reset_store,
)


async def run(ctx) -> None:
    reset_store(ctx)
    recreate_vendor(ctx, checkpoint=0)

    code, _out, err = ctx.app.run(["sync"])
    store = dump_store(ctx) if code == 0 else None
    if store is None:
        ctx.check("crawl_start_clean_backfill_exact",
            False,
            f"sync exit={code} stderr={err[:300]}"
            if code != 0
            else "dump produced no output",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return

    fixtures = fixtures_for(ctx, 0)
    wrong = [e for e in ENTITIES if store[e] != fixtures[e]]
    ctx.check("crawl_start_clean_backfill_exact",
        not wrong,
        "exit=0; all three collections match"
        if not wrong
        else "; ".join(
            f"{e}: rows={len(store[e])} want={len(fixtures[e])}" for e in wrong
        ),
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

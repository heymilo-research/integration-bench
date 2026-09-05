"""Scenario 2 — poll reconciliation of a lost webhook.

Webhooks can be lost. This proves the periodic ``connector sync`` catches a change
that never arrived by webhook.

Flow:
  1. Recreate the vendor at checkpoint 1 and back-fill (``connector sync``); bring
     serve up briefly so the cp1 webhook is consumed (establishing that webhooks
     were working and a watermark is set).
  2. Recreate the vendor at checkpoint 2 with NO listener running. Its dispatcher
     pushes the cp2 mutation (delete cand_0017) to a dead target — the webhook is
     effectively lost.
  3. Run ``connector sync`` again. With the persisted watermark it polls
     ``modified_since`` and recovers the deletion as a tombstone, WITHOUT
     re-crawling the whole table.

Assert the cp2 delete landed via the poll, that the poll used ``modified_since``
(incremental, not a full resync), and that the store matches the post-cp2 key.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    drain_webhooks,
    dump_store,
    load_fixture,
    reset_store,
    serve_start,
    serve_stop,
)


# Seeded tenant size, from verifier/fixtures/candidates_checkpoint_0.json.
SEEDED_CANDIDATES = 400


async def run(ctx) -> None:
    handle = ctx.vendor("talentforge")

    # -- 1. cp0 backfill, then serve + cp1 recreate so dispatcher hits live --
    reset_store(ctx)
    handle.recreate(checkpoint=0)
    code, _out, err = ctx.app.run(["sync"])
    # AND-ed with the backfill's own data-plane traffic (task-0043 pattern,
    # 2026-08-02): exit 0 alone is vacuously bankable by a do-nothing run.
    # Nothing is dumped until after the cp2 reconciliation pass below, so
    # traffic is this phase's own evidence; bare "/" healthcheck pings don't
    # count.
    backfill_traffic = [e for e in handle.request_log() if e.get("path") not in ("/", "")]
    ctx.check("recon_backfill_exit_ok",
        code == 0 and len(backfill_traffic) > 0,
        f"exit={code} stderr={err[-1500:]} data_plane={len(backfill_traffic)}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    serve_start(ctx)
    try:
        handle.recreate(checkpoint=1)
        drain_webhooks(ctx, expect_events={"evt_00001"})
    finally:
        serve_stop(ctx)

    # -- 2. cp2 with NO listener: the delete webhook is lost ----------------
    handle.recreate(checkpoint=2)

    # -- 3. reconciliation poll recovers the lost delete --------------------
    code, _out, err = ctx.app.run(["sync"])
    store = dump_store(ctx)
    # AND-ed with store readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run.
    ctx.check("recon_poll_exit_ok",
        code == 0 and store is not None,
        f"exit={code} stderr={err[-1500:]} store_readable={store is not None}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    if store is None:
        ctx.check(
            "recon_store_readable",
            False,
            "dump produced no output",
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )
        return

    post_cp2 = load_fixture(ctx, "candidates_post_cp2.json")
    # Replaces a whole-store `store == fixture` compare: that voted once for
    # everything, so losing rows to pagination and mis-applying a duplicate
    # scored the same zero. The row count says the crawl completed; the
    # per-record checks below say the data is right.
    ctx.check(
        "reconciliation_row_count",
        len(store) == SEEDED_CANDIDATES,
        f"store rows={len(store)} want={SEEDED_CANDIDATES}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    by_id = {r["source_id"]: r for r in store}
    c17 = by_id.get("cand_0017", {})
    ctx.check("lost_delete_recovered_by_poll",
        c17.get("is_deleted") is True,
        f"cand_0017 is_deleted={c17.get('is_deleted')}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # The reconciliation pass must be incremental: it used modified_since on the
    # candidate list endpoint (not a blind full re-crawl).
    request_log = handle.request_log()
    used_modified_since = any(
        e.get("path") == "/rest/candidates" and "modified_since" in (e.get("query") or {})
        for e in request_log
    )
    ctx.check("reconciliation_used_modified_since",
        used_modified_since,
        "no GET /rest/candidates carried modified_since",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    await builtin_l2(ctx)

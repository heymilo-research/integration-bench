"""watermark_integrity_incremental — a clean incremental pass, proving the
store and the watermark survived the whole restart/expiry drama upstream.

Deliberately does NOT reset the store: it continues from
``budget_pressure_recovery``'s recovered state (scenarios share one postgres
instance for the whole grade). If either recovery had lost rows or mis-anchored
the persisted watermark, THIS is where it surfaces — a recovery that
re-anchored its interrupted pass at the newest ``updated_at`` it happened to
have seen skips every unreached older row permanently, and no later incremental
pass ever looks below the watermark it then confirms.

Recreates the vendor at CHECKPOINT=5 (all 5 seeded mutations applied), with
every fault knob explicitly OFF, and runs a plain ``sync``.

Scoring (2026-08-07):

+2 mandatory : watermark_integrity_rows_exact:{entity} — each collection matches
     the checkpoint-5 fixtures exactly, which is also what proves the 5 seeded
     mutations landed (the fixture carries them, so a separate assertion per
     mutation would only re-score the same bytes). Mandatory because this is a
     failure mode the post-recovery checks upstream cannot see: a recovery can
     leave the store byte-perfect and still have confirmed a watermark that is
     too new, and then this pass — the first one that trusts it — silently misses
     every mutation below it.
+1 : watermark_integrity_no_missing_rows:{entity} — names which rows went
     missing; strictly weaker than the equality check above, hence +1 not +2.
0/-1 : incremental_exit_ok, and watermark_integrity_not_full_resync (the catch-up
     is genuinely incremental — a handful of list requests per collection, not
     the 6/8/5 pages a from-scratch crawl needs). The given starter already
     persists a watermark and already passes both.
L2 : builtin conduct gates/soft checks — scored ONCE per verdict, here. This is
     the only scenario with no verifier-injected vendor traffic, and
     ``builtin_l2`` reads the raw request log with no way to exclude the
     verifier's own requests; scoring it in a probe-bearing scenario graded
     this suite's own probes as connector conduct (measured: a false
     ``no_hot_loop_on_error`` failure against gold, from 8 identical probe
     requests). Scoring the same five prohibitions once per scenario also
     multiplied by four the free credit banked by a connector that is
     well-behaved but wrong about the ticket.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    ENTITIES,
    dump_store,
    fixtures_for,
    list_request_count,
    missing_source_ids,
    recreate_vendor,
)

# A from-scratch crawl of this seed needs 6 (subjects) / 8 (checks) / 5
# (reports) pages; an incremental catch-up over 5 seeded mutations needs one.
# 3 leaves room for a connector that pages differently without leaving room for
# a full re-walk.
_MAX_INCREMENTAL_REQUESTS = 3


async def run(ctx) -> None:
    recreate_vendor(ctx, checkpoint=5)

    code, _out, err = ctx.app.run(["sync"])
    store = dump_store(ctx)
    # AND-ed with store readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run.
    ctx.check("incremental_exit_ok",
        code == 0 and store is not None,
        f"exit={code} stderr={err[:400]} store_readable={store is not None}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    if store is None:
        for entity in ENTITIES:
            ctx.check(
                f"watermark_integrity_rows_exact:{entity}",
                False,
                "dump produced no output",
                pass_value=2,
                fail_value=0,
                mandatory=True,
            )
            ctx.check(
                f"watermark_integrity_no_missing_rows:{entity}",
                False,
                "dump produced no output",
                pass_value=1,
                fail_value=0,
                mandatory=False,
            )
        return

    fixtures = fixtures_for(ctx, 5)
    for entity in ENTITIES:
        rows, fixture = store[entity], fixtures[entity]
        ctx.check(f"watermark_integrity_rows_exact:{entity}",
            rows == fixture,
            f"rows={len(rows)} want={len(fixture)}",
            pass_value=2,
            fail_value=0,
            mandatory=True,
        )
        missing = missing_source_ids(rows, fixture)
        ctx.check(f"watermark_integrity_no_missing_rows:{entity}",
            not missing,
            f"{len(missing)} of {len(fixture)} row(s) missing after the incremental pass: {missing[:5]}",
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )

    # One check across all three collections, not three: it is a single
    # property of the pass ("this was a catch-up, not a re-crawl") and any
    # connector that persisted a watermark at all satisfies it for every
    # entity at once, so splitting it only tripled free credit.
    request_log = [(i, e) for i, e in enumerate(ctx.vendor("vettly").request_log())]
    counts = {e: list_request_count(request_log, f"/v1/{e}") for e in ENTITIES}
    ctx.check("watermark_integrity_not_full_resync",
        all(n <= _MAX_INCREMENTAL_REQUESTS for n in counts.values()),
        f"list requests per collection={counts}; a from-scratch crawl needs 6/8/5",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    await builtin_l2(ctx)

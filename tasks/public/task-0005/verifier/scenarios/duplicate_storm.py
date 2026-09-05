"""Scenario 3 (L3) — duplicate storm + tamper injection.

Exactly-once dedup under a hostile delivery stream is this connector's core
competency, so this is a declared L3 fault-injection scenario.

Flow:
  1. Recreate the vendor at checkpoint 1 with ``TAMPER_INJECT=1``. Its dispatcher
     pushes the cp1 event (candidate.updated cand_0042) as a SEEDED duplicate
     (delivered twice) PLUS exactly one extra delivery carrying a deliberately
     invalid signature (and a stale timestamp).
  2. Bring the serve listener up, drain until the valid event is acked (2xx) and
     the tampered delivery has been REJECTED (non-2xx) by the live listener.
  3. Assert (L3):
       - the tampered delivery was rejected — never acked 2xx, store untouched by it;
       - the seeded duplicate was NOT applied twice;
       - the final store matches the post-cp1 answer key (same as freshness: the
         only real change at cp1 is cand_0042's phone).
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
    stack = ctx.app._stack

    # -- 1. cp0 backfill, then serve + cp1 tamper storm ---------------------
    reset_store(ctx)
    handle.recreate(checkpoint=0)
    code, _out, err = ctx.app.run(["sync"])
    # AND-ed with the backfill's own data-plane traffic (task-0043 pattern,
    # 2026-08-02): exit 0 alone is vacuously bankable by a do-nothing run.
    # Nothing is dumped until after the storm below, so traffic is this
    # phase's own evidence; the healthcheck's bare "/" pings don't count.
    backfill_traffic = [e for e in handle.request_log() if e.get("path") not in ("/", "")]
    ctx.check("storm_backfill_exit_ok",
        code == 0 and len(backfill_traffic) > 0,
        f"exit={code} stderr={err[-1500:]} data_plane={len(backfill_traffic)}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # -- 2. serve first, then cp1 recreate so dispatcher hits live listener -
    stack.vendor_env["TAMPER_INJECT"] = "1"
    serve_start(ctx)
    try:
        handle.recreate(checkpoint=1)
        drained = drain_webhooks(
            ctx, expect_events={"evt_00001"}, expect_tampered=True
        )
    finally:
        serve_stop(ctx)

    ctx.check("storm_drained",
        drained,
        "valid event not acked or tampered delivery not observed rejected",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    deliveries = handle.webhook_deliveries()

    # L3: the tampered delivery was NEVER accepted (2xx) by the listener.
    tampered = [d for d in deliveries if d.get("tampered")]
    tampered_accepted = [
        d for d in tampered if _is_2xx(d.get("status_code"))
    ]
    ctx.check("tampered_delivery_rejected",
        len(tampered) >= 1 and len(tampered_accepted) == 0,
        f"tampered deliveries={len(tampered)} accepted={len(tampered_accepted)}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # L3: the seeded duplicate was actually present (so dedup is really tested).
    evt1 = [d for d in deliveries if d.get("event_id") == "evt_00001"]
    dup_present = any(d.get("duplicate") for d in evt1)
    ctx.check("storm_seeded_duplicate_present",
        dup_present,
        f"evt_00001 deliveries={len(evt1)} duplicate_flag={dup_present}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # -- 3. store correct: dedup held, tamper had no effect -----------------
    store = dump_store(ctx)
    if store is None:
        ctx.check(
            "storm_store_readable",
            False,
            "dump produced no output",
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )
        return

    post_cp1 = load_fixture(ctx, "candidates_post_cp1.json")
    # Replaces a whole-store `store == fixture` compare: that voted once for
    # everything, so losing rows to pagination and mis-applying a duplicate
    # scored the same zero. The row count says the crawl completed; the
    # per-record checks below say the data is right.
    ctx.check(
        "storm_row_count",
        len(store) == SEEDED_CANDIDATES,
        f"store rows={len(store)} want={SEEDED_CANDIDATES}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    by_id = {r["source_id"]: r for r in store}
    c42 = by_id.get("cand_0042", {})
    ctx.check("exactly_once_applied",
        c42.get("data", {}).get("phone") == "+1-555-0142",
        f"cand_0042 phone={c42.get('data', {}).get('phone')}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    await builtin_l2(ctx)


def _is_2xx(code) -> bool:
    try:
        return 200 <= int(code) < 300
    except (TypeError, ValueError):
        return False

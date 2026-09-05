"""replay_and_ooo_composition (L1 + conduct, top rung) -- ``FAULT_OOO_BURST=1``
(reorder window widened to 5 and the whole delivery plan shuffled once) layered
on the same baseline resend traffic, plus ``TAMPER_INJECT=1``.

Where `webhook_freshness_fix` exercises business-time ordering on its own, this
scenario composes it with delivery VOLUME, which is what a real burst brings:

  1. A scripted two-delivery out-of-order pair for one more untouched seed
     record (newest first, older second) -- ordering must still hold while the
     burst's own traffic is in flight.
  2. A flood of distinct, fresh, no-op decoy deliveries (a 404 entity: no row
     touched, no apply journaled -- pure volume), followed by a re-delivery of
     each of the five seeded events with its ORIGINAL payload, freshly signed
     so it is in-skew. That is an ordinary at-least-once re-delivery: the
     vendor makes no promise about how many other events arrive in between
     (docs/webhooks.md, "Re-sends of old events"), so "one event, one apply"
     has to hold across any amount of intervening traffic.

Graded artifacts:

  - `composed_stream_landed` -- rung 1 under the burst: backfill exits 0, all
     five seeded events acked, the mis-signed delivery rejected, and the five
     mutated records match Interviewly's state (snapshot taken before any
     scripted traffic).
  - `journal_no_duplicate_apply::<event_id>` -- ``event_journal.json`` still
     records exactly ONE apply of that event after the flood + re-delivery. A
     re-applied event is invisible in the canonical row (re-fetching the same
     record and rewriting it changes nothing) and visible only here.
  - `row_reflects_newest_event::<id>` / `journal_single_apply::<id>` -- the
     composed ordering pair, same contract as rung 2 of the sibling scenario.
  - `store_matches_upstream_under_burst` -- the whole canonical store equals
     Interviewly's state with exactly the one scripted watermark applied.

Then the built-in L2 gates run ONCE for the task, over the delivery epoch that
carries the most traffic (signature/skew rejection, credential hygiene, token
economy) -- and only if delivery actually happened, so a connector that never
answered a delivery cannot bank a prohibition that passes vacuously on an empty
log.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    drain_webhooks,
    fixture_row,
    flood_decoy_ids,
    inject_run,
    journal_for,
    load_fixture,
    store_diffs,
    read_output,
    reset_connector_state,
    rows_by_id,
    serve_start,
    serve_stop,
    set_faults,
    tamper_rejected,
    wait_for_request_log_quiet,
)

# The seeded timeline: one event per record, and the record each one touches.
NATIVE_EVENTS = [
    ("evt_00001", "interview.rescheduled", "itv_0042", "2026-03-14T11:01:00Z", "interviews"),
    ("evt_00002", "interview.canceled", "itv_0017", "2026-03-14T11:02:00Z", "interviews"),
    ("evt_00003", "interview.scheduled", "itv_9001", "2026-03-14T11:03:00Z", "interviews"),
    ("evt_00004", "feedback.submitted", "fbk_9001", "2026-03-14T11:04:00Z", "feedback"),
    ("evt_00005", "interview.updated", "itv_0099", "2026-03-14T11:05:00Z", "interviews"),
]
ALL_EVENTS = {e[0] for e in NATIVE_EVENTS}
MUTATED_FIXTURE = {
    "itv_0042": "interviews_checkpoint_5.json",
    "itv_0017": "interviews_checkpoint_5.json",
    "itv_9001": "interviews_checkpoint_5.json",
    "itv_0099": "interviews_checkpoint_5.json",
    "fbk_9001": "feedback_checkpoint_5.json",
}

# One more untouched real seed interview for the composed ordering pair.
PAIR_ID = "itv_0250"
PAIR_NEWEST = "2026-03-14T12:20:00Z"
PAIR_OLDER = "2026-03-14T12:05:00Z"

# Enough distinct decoy ids to overflow any fixed-size dedupe structure a
# plausible connector might ship; the number is deliberately not tuned to a
# particular size, which is the property being tested -- "one event, one apply"
# must not depend on intervening volume at all.
DECOY_FLOOD_COUNT = 70
DECOY_ENTITY = "itv_9998"  # not in the seed data -- 404s, so it is a pure no-op
DECOY_ID_PREFIX = "evt_flood_b_"


async def run(ctx) -> None:
    vendor = ctx.vendor("interviewly")

    reset_connector_state(ctx)
    set_faults(ctx, tamper=True)  # clean backfill: no burst yet.
    vendor.recreate(checkpoint=0)
    code, _out, err = ctx.app.run(["sync"])
    backfill_ok = code == 0

    serve_start(ctx)
    pair_statuses: list = []
    redelivery_statuses: list = []
    try:
        set_faults(ctx, ooo_burst=True, tamper=True)
        vendor.recreate(checkpoint=5)
        drained = drain_webhooks(ctx, expect_events=ALL_EVENTS, expect_tampered=True, timeout_s=15.0)
        baseline_rows: dict[str, dict] = {}

        if drained:
            wait_for_request_log_quiet(ctx, quiet_for_s=5.0, timeout_s=100.0)

            snapshot = {
                **rows_by_id(read_output(ctx, "interviews.json")),
                **rows_by_id(read_output(ctx, "feedback.json")),
            }
            baseline_rows = {sid: snapshot.get(sid) for sid in MUTATED_FIXTURE}

            # 1. composed out-of-order pair on one untouched record.
            pair_statuses = inject_run(ctx, [
                {"event_id": f"evt_pair_{PAIR_ID}_b", "event": "interview.updated",
                 "entity_id": PAIR_ID, "occurred_at": PAIR_NEWEST},
                {"event_id": f"evt_pair_{PAIR_ID}_a", "event": "interview.updated",
                 "entity_id": PAIR_ID, "occurred_at": PAIR_OLDER},
            ])

            # 2. volume, then an ordinary at-least-once re-delivery of every
            #    seeded event with its original payload.
            flood_decoy_ids(
                ctx, count=DECOY_FLOOD_COUNT, entity_id=DECOY_ENTITY,
                id_prefix=DECOY_ID_PREFIX,
            )
            redelivery_statuses = inject_run(ctx, [
                {"event_id": event_id, "event": event, "entity_id": entity_id,
                 "occurred_at": occurred_at}
                for event_id, event, entity_id, occurred_at, _table in NATIVE_EVENTS
            ])
    finally:
        serve_stop(ctx)

    mutated_ok = drained and all(
        baseline_rows.get(sid) == fixture_row(ctx, fixture, sid)
        for sid, fixture in MUTATED_FIXTURE.items()
    )
    ctx.check("composed_stream_landed",
        backfill_ok and drained and mutated_ok and tamper_rejected(ctx),
        f"backfill exit={code} stderr={err[:200]!r}; drained={drained}; "
        f"post-drain rows correct={mutated_ok}; tamper_rejected={tamper_rejected(ctx)}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )
    if not drained:
        return

    for event_id, _event, entity_id, occurred_at, _table in NATIVE_EVENTS:
        expected = [{"event_id": event_id, "occurred_at": occurred_at}]
        got = journal_for(ctx, entity_id)
        ctx.check(f"journal_no_duplicate_apply::{event_id}",
            got == expected,
            f"{entity_id}: expected exactly one journaled apply of {event_id} "
            f"({expected}) after {DECOY_FLOOD_COUNT} intervening distinct events and a "
            f"fresh re-delivery (ack statuses={redelivery_statuses}); got={got}",
            pass_value=2,
            fail_value=0,
            mandatory=False,
        )

    interviews = rows_by_id(read_output(ctx, "interviews.json"))
    expected_pair_row = {**fixture_row(ctx, "interviews_checkpoint_5.json", PAIR_ID),
                         "updated_at": PAIR_NEWEST}
    got_pair_row = interviews.get(PAIR_ID)
    ctx.check(f"row_reflects_newest_event::{PAIR_ID}",
        got_pair_row == expected_pair_row,
        f"expected watermark {PAIR_NEWEST} (delivered first) rather than the "
        f"later-arriving older {PAIR_OLDER} (ack statuses={pair_statuses}); got="
        f"{(got_pair_row or {}).get('updated_at')!r}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    expected_pair_journal = [{"event_id": f"evt_pair_{PAIR_ID}_b", "occurred_at": PAIR_NEWEST}]
    got_pair_journal = journal_for(ctx, PAIR_ID)
    ctx.check(f"journal_single_apply::{PAIR_ID}",
        got_pair_journal == expected_pair_journal,
        f"expected exactly one journaled apply ({expected_pair_journal}); got={got_pair_journal}",
        pass_value=2,
        fail_value=0,
        mandatory=False,
    )

    # Same per-row/per-field treatment as scenario 1's rung 3.
    expected_by_table = {
        table: [
            {**row, "updated_at": PAIR_NEWEST} if row["source_id"] == PAIR_ID else row
            for row in load_fixture(ctx, fixture)
        ]
        for table, fixture in (
            ("interviews", "interviews_checkpoint_5.json"),
            ("panelists", "panelists_checkpoint_5.json"),
            ("feedback", "feedback_checkpoint_5.json"),
        )
    }
    detail = store_diffs(ctx, expected_by_table)
    ctx.check("store_rows_exact_under_burst",
        not detail,
        "; ".join(detail) or "all three tables match",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # The task's single builtin_l2 invocation: one vendor epoch's worth of
    # conduct evidence. Running it once per scenario would repeat the same
    # generic gates in the verdict, inflating the do-nothing floor with checks
    # a naive-but-running connector passes for free (WORKLOG 2026-08-01).
    await builtin_l2(ctx)

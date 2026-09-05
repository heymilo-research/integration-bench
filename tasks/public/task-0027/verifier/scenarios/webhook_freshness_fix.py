"""webhook_freshness_fix (L1) -- ordinary traffic lands, and a same-record
out-of-order RUN is resolved by business time rather than arrival order.

No burst fault: baseline delivery only (seeded ``resend_rate: 0.34`` same-
event_id stale re-sends, out-of-order window 3), plus ``TAMPER_INJECT=1``.

Rung 1 -- `baseline_stream_landed`: the checkpoint-5 boot drains (every one of
the five seeded events acked, the deliberately mis-signed delivery rejected),
the backfill exits 0, and the five mutated records match Interviewly's actual
state. Any connector that runs at all reaches this rung; it is measured from a
snapshot taken immediately after the drain, before any scripted traffic.

Rung 2 -- the scripted out-of-order run. Each of four real seed records that
the mutation timeline never touches (so their business fields are constant and
the row's watermark is the only thing that can move) receives THREE distinct,
fresh, correctly-signed deliveries in this arrival order:

    NEWEST occurred_at  ->  OLDEST occurred_at  ->  MIDDLE occurred_at

Only the first is a change worth keeping; the two that follow are older than a
change already applied to the same record (docs/webhooks.md, "Out-of-order
delivery" and the correct-consumer checklist step 4). Two artifacts are graded
per record:

  - `row_reflects_newest_event::<id>` -- the canonical row's watermark is the
    NEWEST event's `occurred_at`, never a later-arriving older one.
  - `journal_single_apply::<id>` -- ``event_journal.json`` records exactly ONE
    apply for that record: the newest event. A connector that applies in
    arrival order journals three entries, two of them regressive; the journal
    is the artifact where that becomes visible even when re-applying the same
    fetched record would leave the canonical row's business fields unchanged.

Rung 3 -- `store_matches_upstream_after_ooo_run`: the whole canonical store
(all three tables, no spurious rows) equals Interviewly's state with exactly
the four scripted watermarks applied.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _scenario_util import (  # noqa: E402
    drain_webhooks,
    fixture_row,
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

ALL_EVENTS = {"evt_00001", "evt_00002", "evt_00003", "evt_00004", "evt_00005"}

# The five records the seeded mutation timeline touches, and the fixture that
# holds their post-mutation truth.
MUTATED = {
    "itv_0042": "interviews_checkpoint_5.json",
    "itv_0017": "interviews_checkpoint_5.json",
    "itv_9001": "interviews_checkpoint_5.json",
    "itv_0099": "interviews_checkpoint_5.json",
    "fbk_9001": "feedback_checkpoint_5.json",
}

# Scripted out-of-order runs: (source_id, table, event type, fixture,
# newest/oldest/middle occurred_at). All four ids are real seed records outside
# the mutation timeline, so `data` is identical in every delivery and the row's
# watermark is the only thing a stale apply can move. Every stamp is later than
# the whole seeded timeline (11:05) so it can never be confused with one.
OOO_RUNS = [
    ("itv_0110", "interviews", "interview.updated", "interviews_checkpoint_5.json",
     "2026-03-14T12:10:00Z", "2026-03-14T11:50:00Z", "2026-03-14T12:00:00Z"),
    ("itv_0160", "interviews", "interview.rescheduled", "interviews_checkpoint_5.json",
     "2026-03-14T12:11:00Z", "2026-03-14T11:51:00Z", "2026-03-14T12:01:00Z"),
    ("itv_0210", "interviews", "interview.updated", "interviews_checkpoint_5.json",
     "2026-03-14T12:12:00Z", "2026-03-14T11:52:00Z", "2026-03-14T12:02:00Z"),
    ("fbk_0060", "feedback", "feedback.submitted", "feedback_checkpoint_5.json",
     "2026-03-14T12:13:00Z", "2026-03-14T11:53:00Z", "2026-03-14T12:03:00Z"),
]


async def run(ctx) -> None:
    vendor = ctx.vendor("interviewly")

    reset_connector_state(ctx)
    set_faults(ctx, tamper=True)  # TAMPER_INJECT=1; no burst, no storm.
    vendor.recreate(checkpoint=0)
    code, _out, err = ctx.app.run(["sync"])
    backfill_ok = code == 0

    serve_start(ctx)
    statuses: dict[str, list] = {}
    try:
        vendor.recreate(checkpoint=5)
        drained = drain_webhooks(ctx, expect_events=ALL_EVENTS, expect_tampered=True)
        baseline_rows: dict[str, dict] = {}
        if drained:
            wait_for_request_log_quiet(ctx, quiet_for_s=5.0, timeout_s=100.0)

            # Snapshot the seeded-timeline records BEFORE any scripted traffic,
            # so rung 1 measures the plain delivery stream on its own.
            snapshot = {
                **rows_by_id(read_output(ctx, "interviews.json")),
                **rows_by_id(read_output(ctx, "feedback.json")),
            }
            baseline_rows = {sid: snapshot.get(sid) for sid in MUTATED}

            for source_id, _table, event, _fixture, newest, oldest, middle in OOO_RUNS:
                statuses[source_id] = inject_run(ctx, [
                    {"event_id": f"evt_ooo_{source_id}_c", "event": event,
                     "entity_id": source_id, "occurred_at": newest},
                    {"event_id": f"evt_ooo_{source_id}_a", "event": event,
                     "entity_id": source_id, "occurred_at": oldest},
                    {"event_id": f"evt_ooo_{source_id}_b", "event": event,
                     "entity_id": source_id, "occurred_at": middle},
                ])
    finally:
        serve_stop(ctx)

    mutated_ok = drained and all(
        baseline_rows.get(sid) == fixture_row(ctx, fixture, sid)
        for sid, fixture in MUTATED.items()
    )
    ctx.check("baseline_stream_landed",
        backfill_ok and drained and mutated_ok and tamper_rejected(ctx),
        f"backfill exit={code} stderr={err[:200]!r}; drained={drained}; "
        f"post-drain rows correct={mutated_ok}; tamper_rejected={tamper_rejected(ctx)}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )
    if not drained:
        return

    interviews = rows_by_id(read_output(ctx, "interviews.json"))
    feedback = rows_by_id(read_output(ctx, "feedback.json"))
    live = {"interviews": interviews, "feedback": feedback}

    for source_id, table, _event, fixture, newest, oldest, middle in OOO_RUNS:
        expected_row = {**fixture_row(ctx, fixture, source_id), "updated_at": newest}
        got_row = live[table].get(source_id)
        ctx.check(f"row_reflects_newest_event::{source_id}",
            got_row == expected_row,
            f"expected watermark {newest} (the newest of the run "
            f"{newest}/{oldest}/{middle}, delivered first); got="
            f"{(got_row or {}).get('updated_at')!r} (ack statuses="
            f"{statuses.get(source_id)})",
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )

        # THE booby trap, per record: a connector that applies in ARRIVAL order
        # journals three applies, two of them regressive. The journal is where
        # that stays visible even when re-applying the same fetched record
        # leaves the canonical row's business fields looking unchanged.
        expected_journal = [{"event_id": f"evt_ooo_{source_id}_c", "occurred_at": newest}]
        got_journal = journal_for(ctx, source_id)
        ctx.check(f"journal_single_apply::{source_id}",
            got_journal == expected_journal,
            f"expected exactly one journaled apply ({expected_journal}); got={got_journal}",
            pass_value=2,
            fail_value=0,
            mandatory=False,
        )

    # Rung 3, as a per-row/per-field diff rather than three whole-table
    # equality compares (`store_matches_upstream_after_ooo_run`). The failure
    # this task produces is a WATERMARK on one row at an unchanged row count,
    # which the old `rows=N expected=M` detail could not describe at all.
    overrides = {source_id: newest for source_id, _t, _e, _f, newest, _o, _m in OOO_RUNS}
    expected_by_table = {
        table: [
            {**row, "updated_at": overrides[row["source_id"]]}
            if row["source_id"] in overrides else row
            for row in load_fixture(ctx, fixture)
        ]
        for table, fixture in (
            ("interviews", "interviews_checkpoint_5.json"),
            ("panelists", "panelists_checkpoint_5.json"),
            ("feedback", "feedback_checkpoint_5.json"),
        )
    }
    detail = store_diffs(ctx, expected_by_table)
    ctx.check("store_rows_exact_after_ooo_run",
        not detail,
        "; ".join(detail) or "all three tables match",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

"""Scenario 1 -- full 4-entity poll backfill + selective webhook consumption
converge on the correct CP2 state, including the job/note updates that ONLY
polling can ever see.

No faults are active in this scenario (that's scenarios 2/3's job) -- this is
the "happy path" combined-surface freshness check.

Flow:
  1. Backfill all 4 entities at checkpoint 0. Assert every entity matches the
     cp0 answer key.
  2. Bring the serve listener up, then step the vendor through checkpoints
     1, 4, and 5 ONE AT A TIME (the mutation timeline: index0 cand_0007
     delete, index1 job_0003 update, index2 note_0004 update, index3
     cand_0055 update, index4 app_0009 delete). The dispatcher only ever
     queues events for the single half-open window ``(checkpoint-1,
     checkpoint]`` on a given boot -- never cumulative, and truncates the
     delivery log on every boot -- so walking 1 -> 4 -> 5 (skipping 2/3,
     which never emit anything since job/note mutations are poll-only) is
     required to observe all three candidate/application events. Each of
     these boots also queues its own seeded tampered delivery.
  3. Drain each step for its expected event id (evt_00001/evt_00004/
     evt_00005) with tampered-rejection required throughout.
  4. Stop serve; run a poll pass. This is the ONLY way job_0003's and
     note_0004's updates are ever discovered (poll-only, no webhook signal
     whatsoever), and it also double-checks the reconcile sweep doesn't
     interfere with (or duplicate) what the webhooks already applied.
  5. Assert the final store matches the post-cp2 answer key for all 4
     entities.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    VENDOR,
    diff_detail,
    drain_checkpoint_events,
    dump_store,
    load_fixture,
    reset_store,
    row_count_ok,
    serve_start,
    serve_stop,
    set_fault_env,
    store_row_diff,
)

STEPS = [
    (1, {"evt_00001"}),
    (4, {"evt_00004"}),
    (5, {"evt_00005"}),
]

_KINDS = ("candidate", "job", "application", "note")
_FIXTURE_NAME = {
    "candidate": "candidates",
    "job": "jobs",
    "application": "applications",
    "note": "notes",
}

_WEBHOOK_TARGETS = (
    ("candidate", "cand_0007", "is_deleted", True),
    ("candidate", "cand_0055", "data.pipeline_status", "placed"),
    ("application", "app_0009", "is_deleted", True),
)


def _webhook_target_state(dumped) -> tuple[bool, str]:
    """Prove the three pushed mutations landed before polling can repair them."""
    if not isinstance(dumped, dict):
        return False, "pre-poll store was unreadable"

    diffs = []
    for kind, source_id, field_path, want in _WEBHOOK_TARGETS:
        rows = dumped.get(kind)
        if not isinstance(rows, list):
            diffs.append(f"{kind} store unreadable")
            continue
        by_id = {
            row.get("source_id"): row
            for row in rows
            if isinstance(row, dict) and row.get("source_id") is not None
        }
        value = by_id.get(source_id)
        for part in field_path.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if value != want:
            diffs.append(f"{source_id}.{field_path}: got={value!r} want={want!r}")

    if diffs:
        return False, "; ".join(diffs)
    return True, "all three candidate/application mutations were present before polling"


def _backfill_state(ctx, dumped: dict) -> None:
    """cp0 backfill, all four kinds. Replaces backfill_{kind}_matches_fixture.

    All 0/-1: MEASURED on the empty probe, the unmodified starter already
    backfills every kind correctly at cp0 (verifier/empty-baseline.json records
    all four old names as empty=True). This task's subject is what happens when a
    NOTIFICATION is withheld, not the crawl, so a correct backfill earns nothing
    here and only losing it costs.
    """
    for kind in _KINDS:
        want = load_fixture(ctx, f"{_FIXTURE_NAME[kind]}_checkpoint_0.json")
        ok, detail = row_count_ok(dumped[kind], want)
        # All *_row_count checks in this task are 0/-1 by MEASUREMENT: TalentLoop
        # TOMBSTONES rather than removing rows, and every mutation in this
        # timeline is an update or a tombstone, so the row count is INVARIANT
        # across the whole checkpoint range and the do-nothing starter passes
        # every one of them (measured: empty scored 8.9/100 when they were +1).
        # All the signal lives in the fields_exact check beside each one; the
        # count survives only as a guard against a pager that duplicates or
        # truncates rows.
        ctx.check(
            f"backfill_row_count:{kind}",
            ok,
            f"{kind}: {detail}",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        diffs = store_row_diff(dumped[kind], want)
        ctx.check(
            f"backfill_fields_exact:{kind}",
            not diffs,
            diff_detail(kind, diffs),
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )


async def run(ctx) -> None:
    handle = ctx.vendor(VENDOR)

    # -- 1. backfill at cp0, no faults --------------------------------------
    reset_store(ctx)
    set_fault_env(ctx)
    handle.recreate(checkpoint=0)

    code, _out, err = ctx.app.run(["backfill"])
    dumped = dump_store(ctx)
    # AND-ed with store readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run.
    ctx.check("backfill_exit_ok",
        code == 0 and dumped is not None,
        f"exit={code} stderr={err[:400]} store_readable={dumped is not None}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    if dumped is None:
        ctx.check(
            "backfill_store_readable",
            False,
            "dump produced no output",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return
    _backfill_state(ctx, dumped)

    # -- 2. step through cp1 -> cp4 -> cp5, draining each individually ------
    serve_start(ctx)
    try:
        delivered, deliveries = drain_checkpoint_events(ctx, STEPS)
    finally:
        serve_stop(ctx)

    pre_poll = dump_store(ctx)
    webhook_state_ok, webhook_state_detail = _webhook_target_state(pre_poll)
    ctx.check("webhook_events_delivered",
        delivered and webhook_state_ok,
        f"all expected events acked_2xx={delivered}; {webhook_state_detail}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    def _is_2xx(code_val) -> bool:
        try:
            return 200 <= int(code_val) < 300
        except (TypeError, ValueError):
            return False

    tampered = [d for d in deliveries if d.get("tampered")]
    tampered_accepted = [d for d in tampered if _is_2xx(d.get("status_code"))]
    ctx.check("tampered_delivery_rejected",
        len(tampered) >= 1 and len(tampered_accepted) == 0,
        f"tampered deliveries={len(tampered)} accepted={len(tampered_accepted)}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # -- 3. poll pass: the ONLY signal for job/note, and must not disturb ---
    #       or duplicate what webhooks already applied.
    code, _out, err = ctx.app.run(["poll"])

    # -- 4. final store reflects every CP2 change, across both surfaces ----
    dumped = dump_store(ctx)
    # AND-ed with store readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run.
    ctx.check("poll_exit_ok",
        code == 0 and dumped is not None,
        f"exit={code} stderr={err[:400]} store_readable={dumped is not None}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    if dumped is None:
        ctx.check(
            "freshness_store_readable",
            False,
            "dump produced no output",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return
    # cp2 convergence across BOTH surfaces. Replaces freshness_{kind}_matches_
    # fixture. +1 and discriminating: the starter fails all four (measured), since
    # job_0003 and note_0004 emit no webhook at all and are poll-only.
    for kind in _KINDS:
        want = load_fixture(ctx, f"{_FIXTURE_NAME[kind]}_post_cp2.json")
        ok, detail = row_count_ok(dumped[kind], want)
        ctx.check(
            f"freshness_row_count:{kind}",
            ok,
            f"{kind}: {detail}",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        diffs = store_row_diff(dumped[kind], want)
        ctx.check(
            f"freshness_fields_exact:{kind}",
            not diffs,
            diff_detail(kind, diffs),
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )

    candidates_by_id = {r["source_id"]: r for r in dumped["candidate"]}
    c7 = candidates_by_id.get("cand_0007", {})
    ctx.check("webhook_applied_delete::cand_0007", c7.get("is_deleted") is True,
                f"cand_0007 is_deleted={c7.get('is_deleted')}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    c55 = candidates_by_id.get("cand_0055", {})
    ctx.check("webhook_applied_update::cand_0055",
        c55.get("data", {}).get("pipeline_status") == "placed",
        f"cand_0055 pipeline_status={c55.get('data', {}).get('pipeline_status')}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )
    applications_by_id = {r["source_id"]: r for r in dumped["application"]}
    a9 = applications_by_id.get("app_0009", {})
    ctx.check("webhook_applied_delete::app_0009", a9.get("is_deleted") is True,
                f"app_0009 is_deleted={a9.get('is_deleted')}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    jobs_by_id = {r["source_id"]: r for r in dumped["job"]}
    j3 = jobs_by_id.get("job_0003", {})
    ctx.check("poll_applied_update::job_0003",
        j3.get("data", {}).get("status") == "closed",
        f"job_0003 status={j3.get('data', {}).get('status')}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    notes_by_id = {r["source_id"]: r for r in dumped["note"]}
    n4 = notes_by_id.get("note_0004", {})
    ctx.check("poll_applied_update::note_0004",
        n4.get("data", {}).get("body") == "Updated after debrief.",
        f"note_0004 body={n4.get('data', {}).get('body')}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    await builtin_l2(ctx)

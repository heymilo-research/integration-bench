"""Scenario 2 — one reported success that never lands (top rung).

Same batch shape as bulk_ingest_mixed_results, but this scenario turns on
``SL_LYING_REF=batch-0007`` and ``SL_RAW_LAG_REQS=3`` on top of
``SL_BULK_ENABLED``. batch-0007 — an item that behaves as an ordinary real
create in scenario 1 — now reports a clean `201` with a fabricated id on its
first submission, and that id is never actually persisted: it never appears
in any read, no matter how many follow. Every OTHER real create in this same
batch is genuinely delayed (not fake) by ``SL_RAW_LAG_REQS`` further reads
against the candidates collection, request-indexed, since the write itself
never advances that counter.

A correct connector must not trust the bulk response's `201` at face value:
it schedules a reconciliation read, and only marks batch-0007 as `created`
false once that reconciliation is sequenced past the lag horizon and still
finds nothing — while the other, genuinely-lagged real creates DO eventually
resolve. Confusing "not yet visible" with "never existed" fails this
scenario either way (rung 4 if it trusts the lie; rung 5 if it gives up too
early on a real one).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    BATCH_FIELDS,
    candidate_exists_by_fields,
    clear_result,
    get_by_id_entries,
    load_fixture,
    read_result,
    recreate_vendor,
    reset_store,
    vendor_crawl_candidates,
)

ALL_REFS = [f"batch-{n:04d}" for n in range(1, 11)]
LYING_REF = "batch-0007"


async def run(ctx) -> None:
    handle = ctx.vendor("staffline")
    recreate_vendor(ctx, checkpoint=0, sl_lying_ref=LYING_REF, sl_raw_lag_reqs=3)
    reset_store(ctx)
    clear_result(ctx)

    code, _out, err = ctx.app.run(["push"])
    result = read_result(ctx) if code == 0 else None
    ctx.check(
        "push_completed_and_output_readable",
        code == 0 and result is not None,
        f"exit={code} stderr={err[:400]}" if code != 0 else "push exited 0 but wrote no readable output",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )
    if result is None:
        return

    items = result.get("items", [])
    by_ref = {i.get("client_ref"): i for i in items}

    fixture = load_fixture(ctx, "lying_success_reconcile.json")
    assert fixture["lying_ref"] == LYING_REF

    # --- Rung 4 (top rung): lying_success_detected_via_reconciliation -------
    lying_entry = by_ref.get(LYING_REF, {})
    crawl = vendor_crawl_candidates(ctx)
    lying_fields = BATCH_FIELDS[LYING_REF]
    lying_really_exists = candidate_exists_by_fields(crawl, **lying_fields)
    ctx.check(
        "lying_ref_marked_not_created_and_independently_confirmed_absent",
        lying_entry.get("created") is False and not lying_really_exists,
        f"{LYING_REF}: recorded={lying_entry} live_vendor_has_matching_row={lying_really_exists} "
        f"(a clean 201 + fabricated id must NOT be trusted at face value; confirmed "
        f"directly against the live vendor, not merely trusted from the connector's own output)",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    ctx.check(
        "lying_ref_output_never_reports_an_id",
        lying_entry.get("id") is None,
        f"{LYING_REF}: id={lying_entry.get('id')!r} (a record that was never "
        f"confirmed present must never carry an id in the recorded outcome, "
        f"even if StaffLine's immediate response reported one)",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # Comprehensive, connector-independent cross-check: EVERY ref the output
    # claims created must genuinely exist on the live vendor by content, and
    # every ref it claims failed must genuinely not -- this is what actually
    # separates "trusted the lie" from "did the reconciliation work" (a
    # connector that trusts every 201 blindly gets this wrong for the lying
    # ref specifically, even though its OTHER claims happen to be right).
    mismatches = []
    for ref in ALL_REFS:
        fields = BATCH_FIELDS.get(ref)
        if fields is None:  # a permanently-422 ref was never even attempted
            continue
        exists = candidate_exists_by_fields(crawl, **fields)
        claimed_created = bool(by_ref.get(ref, {}).get("created"))
        if exists != claimed_created:
            mismatches.append({"client_ref": ref, "claimed": claimed_created, "live": exists})
    ctx.check(
        "creates_output_matches_true_vendor_state",
        len(mismatches) == 0,
        f"{len(mismatches)} ref(s) where the recorded outcome disagrees with the "
        f"live vendor's actual state: {mismatches}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # Bookkeeping completeness, kept as a set/field comparison rather than the
    # whole-document blob compare this replaces: every ref must be reported
    # exactly once, the created/failed split must match the answer key, and
    # only genuinely-created refs may carry an id. This is what closes the
    # inverse loophole in creates_output_matches_true_vendor_state above -- a
    # connector that reports EVERYTHING as failed agrees with the (empty) live
    # vendor and would otherwise find zero mismatches.
    got_created = {ref for ref, i in by_ref.items() if i.get("created")}
    got_failed = {ref for ref, i in by_ref.items() if not i.get("created")}
    ctx.check(
        "lying_scenario_status_bookkeeping_matches_fixture",
        sorted(r for r in by_ref if r) == sorted(ALL_REFS)
        and len(items) == len(ALL_REFS)
        and got_created == set(fixture["created_refs"])
        and got_failed == set(fixture["failed_refs"])
        and all(by_ref[r].get("id") for r in got_created)
        and all(by_ref[r].get("id") is None for r in got_failed),
        f"got {len(items)} item(s); created={sorted(got_created)} failed={sorted(got_failed)} "
        f"(expected created={sorted(fixture['created_refs'])} "
        f"failed={sorted(fixture['failed_refs'])})",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # --- Rung 5: lag_not_mistaken_for_lying ----------------------------------
    request_log = handle.request_log()
    read_entries = get_by_id_entries(request_log)
    ctx.check(
        "reconciliation_attempted_at_all",
        len(read_entries) >= 1,
        f"{len(read_entries)} GET-by-id read(s) issued -- a 201 must be "
        f"reconciled, never trusted purely from the bulk response itself",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    if read_entries:
        ctx.check(
            "reconciliation_not_abandoned_after_one_read",
            len(read_entries) >= 2,
            f"only {len(read_entries)} GET-by-id read(s) issued -- distinguishing "
            f"a genuinely-lagged real row from a permanently-fabricated one "
            f"requires more than a single negative read before giving up",
            pass_value=2,
            fail_value=0,
            mandatory=True,
        )

    await builtin_l2(ctx)

"""Scenario 1 — the 207 envelope, parsed and bookkept correctly.

A single scripted batch (``repo/input/candidate_batch.json``, 10 items) is
submitted to ``POST /svc/candidates/bulk`` with ``SL_LYING_REF`` and
``SL_RAW_LAG_REQS`` both unset (bulk_ingest_mixed_results only turns on
``SL_BULK_ENABLED``). By construction the first submission lands on all three
non-lying per-item statuses:

  - position 0,4,5,6,9 (batch-0001/0005/0006/0007/0010) -> 201
  - position 1,2,7     (batch-0002/0003/0008)           -> 422 (permanent)
  - position 3,8       (batch-0004/0009)                -> 503 (transient)

Two of these ten items are deterministically transient, so ANY connector that
competently uses the bulk endpoint at all must issue more than one bulk
submission to have a chance at full correctness — that is this scenario's
cheap, dense floor for rung 1. A correct connector then retries only those
two transient items (by the SAME client_ref, never the whole batch) and ends
with 7 created / 3 permanently failed. Rungs 1-3 of the ladder live here.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    bulk_payload_fields,
    bulk_payload_refs,
    bulk_post_entries,
    clear_result,
    load_fixture,
    read_result,
    recreate_vendor,
    reset_store,
)

ALL_REFS = [f"batch-{n:04d}" for n in range(1, 11)]
PERMANENT_REFS = ["batch-0002", "batch-0003", "batch-0008"]
TRANSIENT_REFS = ["batch-0004", "batch-0009"]


async def run(ctx) -> None:
    handle = ctx.vendor("staffline")
    recreate_vendor(ctx, checkpoint=0)
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

    fixture = load_fixture(ctx, "bulk_ingest_mixed_results.json")
    created_set = set(fixture["created_refs"])
    failed_set = set(fixture["failed_refs"])

    # --- Rung 1: bulk_submission_parses_per_item_results --------------------
    got_created = {ref for ref, i in by_ref.items() if i.get("created")}
    got_failed = {ref for ref, i in by_ref.items() if not i.get("created")}
    ctx.check(
        "mixed_status_bookkeeping_matches_fixture",
        sorted(r for r in by_ref if r) == sorted(ALL_REFS)
        and len(items) == len(ALL_REFS)
        and got_created == created_set
        and got_failed == failed_set
        and all(by_ref[r].get("id") for r in got_created)
        and all(by_ref[r].get("id") is None for r in got_failed),
        f"got {len(items)} item(s); created={sorted(got_created)} failed={sorted(got_failed)} "
        f"(expected created={sorted(created_set)} failed={sorted(failed_set)})",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    request_log = handle.request_log()
    bulk_entries = bulk_post_entries(request_log)

    ctx.check(
        "mixed_multiple_bulk_calls_issued",
        len(bulk_entries) >= 2,
        f"{len(bulk_entries)} bulk submission(s) -- this batch has 2 deterministic "
        f"transient (503) items on first submission, so any connector that reads "
        f"the envelope and reacts to it at all issues more than one bulk call",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # --- Rung 3: transient_503_retried_to_success ----------------------------
    transient_counts = {ref: 0 for ref in TRANSIENT_REFS}
    for e in bulk_entries:
        for ref in bulk_payload_refs(e):
            if ref in transient_counts:
                transient_counts[ref] += 1
    ctx.check(
        "transient_503_items_created_after_retry",
        all(by_ref.get(r, {}).get("created") for r in TRANSIENT_REFS),
        f"{ {r: by_ref.get(r, {}).get('created') for r in TRANSIENT_REFS} }",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    ctx.check(
        "transient_items_retried_by_same_client_ref",
        all(c == 2 for c in transient_counts.values()),
        f"submission counts per transient ref (expect exactly 2: initial + one "
        f"retry, keyed off the SAME client_ref): {transient_counts}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # --- Rung 2: permanent_422_not_retried (traffic-conditional) -------------
    # Scored only once a resubmission has actually happened at all (otherwise
    # "never resubmitted" is trivially true of everything and proves nothing —
    # see bench.verifier.builtin_l2's module docstring on evidence slices).
    if len(bulk_entries) > 1:
        permanent_counts = {ref: 0 for ref in PERMANENT_REFS}
        for e in bulk_entries:
            for ref in bulk_payload_refs(e):
                if ref in permanent_counts:
                    permanent_counts[ref] += 1
        ctx.check(
            "permanents_not_retried",
            all(c == 1 for c in permanent_counts.values()),
            f"submission counts per permanent ref (expect exactly 1 each, even "
            f"across multiple bulk calls): {permanent_counts}",
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )

        # A retry must resubmit the SAME item, not some other-fields payload
        # for that client_ref -- data integrity of the retry itself.
        original_fields = {
            ref: bulk_payload_fields(bulk_entries[0], ref) for ref in TRANSIENT_REFS
        }
        retry_fields_match = all(
            any(
                bulk_payload_fields(e, ref) == original_fields[ref]
                for e in bulk_entries[1:]
                if ref in bulk_payload_refs(e)
            )
            for ref in TRANSIENT_REFS
        )
        ctx.check(
            "retry_payload_preserves_original_fields",
            retry_fields_match,
            "a retried transient item's resubmitted fields did not match what "
            "was originally submitted for that client_ref",
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )

    await builtin_l2(ctx)

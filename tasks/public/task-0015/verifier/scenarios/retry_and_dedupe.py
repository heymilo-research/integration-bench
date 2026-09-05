"""Scenario 3 — resubmitting the whole batch never re-creates anything.

Same env as bulk_ingest_mixed_results (SL_BULK_ENABLED=1 only). ``push`` runs
TWICE against the SAME live vendor with the SAME staged batch — a legitimate
re-run (a crash, an operator retry, uncertainty about whether the first run
finished; the ticket's own migration-tool framing invites exactly this). The
durable outcome after the second push must be byte-identical to after the
first: no missing items, no duplicates, and in particular no regression on
items that were already correctly resolved.

The exact-count comparison against the fixture (unchanged from
bulk_ingest_mixed_results.json, since this scenario uses the same env) is the
check a batch-replay bug cannot pass, whatever caused it. The traffic-
conditional L3 below only fires when the request log shows a resubmission
actually happened on the wire, and checks two independent, narrower things: no
freshly-minted client_ref was ever used to dodge dedup, and no ref's outcome
regressed across the replay.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    bulk_payload_refs,
    bulk_post_entries,
    clear_result,
    load_fixture,
    read_result,
    recreate_vendor,
    reset_store,
)

ALL_REFS = [f"batch-{n:04d}" for n in range(1, 11)]


async def run(ctx) -> None:
    handle = ctx.vendor("staffline")
    recreate_vendor(ctx, checkpoint=0)
    reset_store(ctx)
    clear_result(ctx)

    fixture = load_fixture(ctx, "bulk_ingest_mixed_results.json")
    created_set = set(fixture["created_refs"])
    failed_set = set(fixture["failed_refs"])

    # -- first push -----------------------------------------------------------
    code1, _o1, err1 = ctx.app.run(["push"])
    result1 = read_result(ctx) if code1 == 0 else None
    if result1 is None:
        ctx.check(
            "both_pushes_completed_and_readable",
            False,
            f"first push: exit={code1} stderr={err1[:400]}",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return
    by_ref1 = {i.get("client_ref"): i for i in result1.get("items", [])}

    # The first push's own correctness, as a set/field comparison rather than
    # the whole-document blob compare this replaces. Transitively implied by
    # no_batch_replay_duplicates_exact_count AND second_push_did_not_regress_*
    # below, but kept explicit so a task that fails BOTH pushes identically is
    # attributed here rather than reading as a replay bug.
    got_created1 = {ref for ref, i in by_ref1.items() if i.get("created")}
    got_failed1 = {ref for ref, i in by_ref1.items() if not i.get("created")}
    ctx.check(
        "first_push_matches_fixture",
        sorted(r for r in by_ref1 if r) == sorted(ALL_REFS)
        and len(result1.get("items", [])) == len(ALL_REFS)
        and got_created1 == created_set
        and got_failed1 == failed_set
        and all(by_ref1[r].get("id") for r in got_created1)
        and all(by_ref1[r].get("id") is None for r in got_failed1),
        f"after 1st push: got {len(result1.get('items', []))} item(s); "
        f"created={sorted(got_created1)} failed={sorted(got_failed1)} "
        f"(expected created={sorted(created_set)} failed={sorted(failed_set)})",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # -- second push: resubmit the SAME batch against the SAME live vendor ---
    clear_result(ctx)
    code2, _o2, err2 = ctx.app.run(["push"])
    result2 = read_result(ctx) if code2 == 0 else None
    ctx.check(
        "both_pushes_completed_and_readable",
        code2 == 0 and result2 is not None,
        f"second push: exit={code2} stderr={err2[:400]}" if code2 != 0 or result2 is None else "",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )
    if result2 is None:
        return

    by_ref2 = {i.get("client_ref"): i for i in result2.get("items", [])}
    got_created2 = {ref for ref, i in by_ref2.items() if i.get("created")}
    got_failed2 = {ref for ref, i in by_ref2.items() if not i.get("created")}

    # Exact-count L1: byte-identical outcome after the replay. This is the
    # check a batch-replay bug cannot pass, regardless of how it went wrong.
    ctx.check(
        "no_batch_replay_duplicates_exact_count",
        got_created2 == created_set
        and got_failed2 == failed_set
        and len(result2.get("items", [])) == len(ALL_REFS),
        f"after 2nd push: created={sorted(got_created2)} failed={sorted(got_failed2)} "
        f"count={len(result2.get('items', []))} (expected {len(ALL_REFS)} total, "
        f"created={sorted(created_set)}, failed={sorted(failed_set)})",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    ctx.check(
        "second_push_did_not_regress_first_push_result",
        result1 == result2,
        "the two pushes' recorded outcomes differ -- resubmitting an "
        "already-resolved batch must never change an item's recorded outcome",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # --- Rung 6 (traffic-conditional): no_batch_replay_duplicates -----------
    request_log = handle.request_log()
    bulk_entries = bulk_post_entries(request_log)
    all_submitted_refs = {ref for e in bulk_entries for ref in bulk_payload_refs(e)}
    refs_seen_more_than_once = [
        ref for ref in ALL_REFS
        if sum(1 for e in bulk_entries if ref in bulk_payload_refs(e)) > 1
    ]
    # Scored only when a replay actually happened on the wire (some ref was
    # submitted more than once across the two pushes). A connector that
    # durably remembers already-resolved refs and skips resubmitting them
    # entirely makes this vacuously absent, which is the ideal outcome, not a
    # loophole (see bench.verifier.builtin_l2's module docstring on why a
    # prohibition needs its own evidence slice, not "any traffic at all").
    if refs_seen_more_than_once:
        no_fresh_refs_minted = all_submitted_refs <= set(ALL_REFS)
        no_regression_on_replayed_refs = all(
            not (by_ref1.get(ref, {}).get("created") and not by_ref2.get(ref, {}).get("created"))
            for ref in refs_seen_more_than_once
        )
        ctx.check(
            "no_batch_replay_duplicates",
            no_fresh_refs_minted and no_regression_on_replayed_refs,
            f"overlapping refs were {refs_seen_more_than_once}; "
            f"no_fresh_refs_minted={no_fresh_refs_minted} (dedupe must be keyed off "
            f"the input's own client_ref, never a freshly-minted one) "
            f"no_regression_on_replayed_refs={no_regression_on_replayed_refs} "
            f"(a replay must never un-create an already-created record)",
            pass_value=1,
            fail_value=0,
            mandatory=False,
        )

    await builtin_l2(ctx)

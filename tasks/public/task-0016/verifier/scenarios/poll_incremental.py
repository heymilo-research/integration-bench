"""Scenario 3 — incremental catch-up via a persisted epoch-SECONDS watermark.

Flow:
  1. Recreate the vendor at checkpoint 0 and back-fill (``poll``). This crawls
     every candidate (1-based paging) and persists the watermark = max
     ``updated_ts`` seen (epoch seconds).
  2. Advance the tenant's timeline: recreate the vendor at checkpoint 5, which
     applies the candidate mutations on top of the base data — an update
     (cand_0042 phone / stage), a delete (cand_0017 -> tombstone), a create
     (cand_0900), and another update (cand_0099 stage).
  3. Run ``poll`` again. With the persisted watermark it polls ``modified_since``
     and pulls ONLY the changed candidates (not the whole table), applying the
     update, the tombstone, and the new row.

This bites all three stale-doc lies at once:
  - the last-modified field is ``updated_ts`` (docs call it ``last_modified``) —
    a docs-following connector never sets a valid watermark;
  - that value is epoch SECONDS (docs claim millis) — a millis watermark is far-
    future and the incremental poll returns nothing (misses every change); and
  - pages are 1-BASED (docs' sample loop starts at ``page=0``, which clamps to
    page 1 and double-reads it) — a 0-based crawl duplicates page-1 ids.
A connector that trips any of these produces a store that does not match the key.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    VENDOR,
    candidate_list_pages,
    clear_outputs,
    diff_detail,
    load_fixture,
    read_candidates,
    row_diff,
)


async def run(ctx) -> None:
    handle = ctx.vendor(VENDOR)

    # -- 1. cp0 backfill (establishes the watermark) ------------------------
    handle.recreate(checkpoint=0)
    clear_outputs(ctx)
    code, _out, err = ctx.app.run(["poll"])

    backfill = read_candidates(ctx)
    # AND-ed with output readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run. clear_outputs() above
    # guarantees this file can only come from THIS pass.
    ctx.check(
        "backfill_exit_ok",
        code == 0 and backfill is not None,
        f"exit={code} stderr={err[:400]} output_readable={backfill is not None}",
        pass_value=0, fail_value=-1, mandatory=False,
    )

    if backfill is None:
        ctx.check(
            "backfill_store_readable", False, "backfill produced no output",
            pass_value=0, fail_value=-1, mandatory=False,
        )
        return
    ctx.check(
        "backfill_store_readable", True, "",
        pass_value=0, fail_value=-1, mandatory=False,
    )

    # The backfill crawl paged to exhaustion (120 candidates / 25 per page => 5
    # pages) and every row's updated_at parsed as a real integer (timestamp not
    # dropped / mis-mapped). A 1-based crawl yields exactly `total` rows, no dups.
    ids = [r["source_id"] for r in backfill]
    ctx.check(
        "backfill_no_duplicate_rows",
        len(ids) == len(set(ids)) and len(ids) == 120,
        f"rows={len(ids)} distinct={len(set(ids))} (a page=0 crawl double-reads page 1)",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    # `bool(backfill)` is load-bearing: `all()` over zero rows is True, so a
    # connector that synced NOTHING passed this mandatory +2 check. Measured
    # 2026-08-08: gold has 120 rows, the empty starter never reaches this line at
    # all (so the check was absent from its verdict rather than passing in it —
    # which is why the empty probe could not flag it, and a PARTIAL submission
    # is what would have collected the points). The row COUNT is already graded
    # by backfill_no_duplicate_rows above; this guard only has to establish that
    # "every row" is quantifying over something.
    non_int = [r.get("updated_at") for r in backfill
               if not isinstance(r.get("updated_at"), int)]
    ctx.check(
        "backfill_updated_at_is_int",
        bool(backfill) and not non_int,
        f"rows={len(backfill)} (need >= 1) non_integer_updated_at={non_int[:5]} "
        "(timestamp mis-parsed / wrong field)",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    backfill_log = handle.request_log()
    backfill_pages = candidate_list_pages(backfill_log)
    ctx.check(
        "backfill_paged_to_exhaustion",
        len(backfill_pages) >= 5,
        f"only {len(backfill_pages)} candidate list request(s); expected >= 5 pages",
        pass_value=1, fail_value=0, mandatory=False,
    )

    # -- 2. advance the timeline to cp5 -------------------------------------
    handle.recreate(checkpoint=5)

    # -- 3. incremental reconciliation poll ---------------------------------
    code, _out, err = ctx.app.run(["poll"])
    ctx.check(
        "incr_poll_exit_ok", code == 0, f"exit={code} stderr={err[:400]}",
        pass_value=0, fail_value=-1, mandatory=False,
    )

    store = read_candidates(ctx)
    if store is None:
        ctx.check(
            "incr_store_readable", False, "incremental poll produced no output",
            pass_value=0, fail_value=-1, mandatory=False,
        )
        return

    # Whole-store convergence on the cp5 answer key, as a per-row/per-field diff
    # rather than the old `store == cp5` blob compare (`incremental_matches_
    # fixture`). The three targeted checks below cover cand_0042 / cand_0017 /
    # cand_0900 individually; this one additionally covers cand_0099's stage
    # update, every unchanged row's fields, and the row count -- i.e. everything
    # a connector that resyncs the whole table with a broken watermark, or
    # applies a mutation to the wrong row, gets wrong.
    cp5 = load_fixture(ctx, "candidates_checkpoint_5.json")
    got_ids = [r.get("source_id") for r in store]
    ctx.check(
        "incremental_row_count",
        len(got_ids) == len(cp5) and len(set(got_ids)) == len(got_ids),
        f"rows={len(got_ids)} distinct={len(set(got_ids))} expected={len(cp5)}",
        pass_value=1, fail_value=0, mandatory=False,
    )
    diffs = row_diff(store, cp5)
    ctx.check(
        "incremental_fields_exact",
        not diffs,
        diff_detail("candidates", diffs),
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    by_id = {r["source_id"]: r for r in store}

    # The update landed (cand_0042 phone bumped).
    c42 = by_id.get("cand_0042", {})
    ctx.check(
        "incremental_applied_update",
        c42.get("data", {}).get("phone") == "+1-555-0142",
        f"cand_0042 phone={c42.get('data', {}).get('phone')}",
        pass_value=1, fail_value=0, mandatory=False,
    )

    # The delete landed as a tombstone (row retained, is_deleted true).
    c17 = by_id.get("cand_0017", {})
    ctx.check(
        "incremental_applied_tombstone",
        c17.get("is_deleted") is True,
        f"cand_0017 is_deleted={c17.get('is_deleted')}",
        pass_value=1, fail_value=0, mandatory=False,
    )

    # The create landed (new candidate present exactly once).
    c900 = by_id.get("cand_0900")
    ctx.check(
        "incremental_applied_create",
        c900 is not None,
        "cand_0900 was never inserted by the incremental poll",
        pass_value=1, fail_value=0, mandatory=False,
    )

    incr_log = handle.request_log()

    # The reconciliation pass used modified_since, and it was formatted as epoch
    # SECONDS (a 10-digit integer). A millis watermark (13-digit) is far-future
    # and would return zero changed rows — silently missing every mutation.
    incr_pages = candidate_list_pages(incr_log, with_modified_since=True)
    ctx.check(
        "incremental_used_modified_since",
        len(incr_pages) >= 1,
        "no GET /v1/candidates carried modified_since on the incremental pass",
        pass_value=1, fail_value=0, mandatory=False,
    )
    ms_values = [(e.get("query") or {}).get("modified_since") for e in incr_pages]
    # The `len(...) >= 1` is the whole point of this line. `all()` over an empty
    # list is True, so before 2026-08-08 a connector that sent NO modified_since
    # passed this mandatory +2 check — the exact failure the check is named for.
    # The empty starter could not expose it (it exits before the incremental
    # pass, so the check never appears in its verdict); a partial submission that
    # runs the pass but ignores the watermark would have banked the points.
    #
    # 1 is the MEASURED gap, not a default: gold sends exactly one watermarked
    # request here (`['1773480540']`, 2026-08-08) because the incremental result
    # set is a single page, so no larger threshold is available.
    all_seconds = len(ms_values) >= 1 and all(
        v is not None and str(v).isdigit() and len(str(v)) == 10 for v in ms_values
    )
    ctx.check(
        "watermark_sent_as_epoch_seconds",
        all_seconds,
        f"modified_since values={ms_values} (need >= 1; each must be 10-digit "
        "epoch seconds, not 13-digit millis)",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # And it did NOT re-crawl the whole table on the incremental pass: the poll
    # that carried modified_since returns a tiny filtered result set (one page).
    ctx.check(
        "incremental_not_full_resync",
        1 <= len(incr_pages) <= 2,
        f"{len(incr_pages)} modified_since candidate page(s); a full re-crawl would be >= 5",
        pass_value=1, fail_value=0, mandatory=False,
    )

    await builtin_l2(ctx)

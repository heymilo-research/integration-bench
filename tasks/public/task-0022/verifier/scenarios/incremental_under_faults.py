"""incremental_under_faults — backfill at CP0, advance to CP1 (candidate
mutations: update / tombstone / create), then run an incremental catch-up
while offset 0 (the only page in the small filtered result set) returns 500
twice, with the rate limiter still enabled.

The fault env is a single per-process boot var, so this scenario overrides it
to ``0:2`` (the incremental result set at CP1 is only ~3 candidates — one page
at offset 0) and recreates the vendor at CP1 — a fresh boot resets the fault
hit-counter budget.

L1  : app exits 0; row count is 6001; update / tombstone-retained-delete /
      create all landed.
L3  : resume_not_restart — the faulted offset 0 is retried to a 200;
      exactly_once — no duplicate source_ids, count matches the fixture;
      no_retry_after_violation_on_incremental — a Retry-After violation costs,
      but nothing is earned: the limiter provably cannot fire in this phase
      (measured rate_limited_hits=0 for gold), so the check can only pass by
      silence. Deliberately NOT named `retry_after_honored_l3`, which is the
      backfill scenario's name for the stronger, evidence-backed assertion.
L2  : builtin conduct gates/soft checks.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2
from bench.verifier import logs

from _scenario_util import (  # noqa: E402
    diff_detail,
    dump_store,
    load_fixture,
    reset_store,
    row_diff,
)

_FAULT_OFFSET = 0


def _offset(entry) -> int:
    try:
        return int((entry.get("query") or {}).get("offset") or 0)
    except (TypeError, ValueError):
        return -1


def _candidate_pages(log):
    return sorted(
        (e for e in log
         if e.get("method") == "GET" and e.get("path") == "/v1/candidates"),
        key=lambda e: e.get("ts", 0),
    )


async def run(ctx) -> None:
    vendor = ctx.vendor("globalhire")

    # -- 1. CP0 backfill (establishes the watermark), no faults matter here --
    reset_store(ctx)
    vendor.recreate(checkpoint=0)
    marker_ts = max((e.get("ts", 0) for e in vendor.request_log()), default=-1.0)
    code, _out, err = ctx.app.run(["sync"])
    # AND-ed with this phase's own data-plane traffic (task-0043 pattern,
    # 2026-08-02): exit 0 alone is vacuously bankable by a do-nothing run, and
    # nothing is dumped until after the CP1 incremental pass below.
    backfill_calls = [
        e for e in vendor.request_log()
        if e.get("ts", 0) > marker_ts and str(e.get("path", "")).startswith("/v1/")
    ]
    # No faults are armed for this CP0 phase, so an unmodified starter already
    # completes it cleanly — passing earns nothing, only a regression costs.
    ctx.check(
        "incr_backfill_exit_ok",
        code == 0 and len(backfill_calls) > 0,
        f"exit={code} data_plane_calls={len(backfill_calls)} stderr={err[:400]}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # -- 2. Retarget the fault into the small incremental result set, CP1 ----
    vendor._stack.vendor_env["FAULT_5XX_ON_PAGE"] = "0:2"
    vendor.recreate(checkpoint=1)

    # -- 3. incremental reconciliation poll under fault pressure -------------
    exit_code, _out, err = ctx.app.run(["sync"])

    store = dump_store(ctx)
    # AND-ed with store readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run. "Ran / output readable"
    # is exactly the pattern a do-nothing submission could win by executing —
    # pass=0, only a regression (crash / unreadable output) costs.
    ctx.check(
        "app_exit_ok",
        exit_code == 0 and store is not None,
        f"exit={exit_code} stderr={err[:500]} store_readable={store is not None}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    if store is None:
        ctx.check(
            "incr_store_readable",
            False,
            "dump produced no output",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return

    fixture = load_fixture(ctx, "candidates_incremental.json")
    # Restores the signal of the deleted `incremental_matches_fixture` blob
    # compare. The three `incremental_applied_*` checks below cover cand_00042,
    # cand_00017 and cand_09000 only; the other 5,998 rows, and every field of
    # the three named ones beyond the single attribute each asserts, were
    # ungraded after the deletion.
    diffs = row_diff(store, fixture)
    ctx.check(
        "incremental_rows_exact",
        not diffs,
        diff_detail("candidates_incremental", store, fixture, diffs),
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    ctx.check(
        "incremental_row_count_6001",
        len(store) == 6001,
        f"store rows={len(store)} (expected 6001)",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    by_id = {r["source_id"]: r for r in store}
    c42 = by_id.get("cand_00042", {})
    ctx.check(
        "incremental_applied_update",
        c42.get("data", {}).get("pipeline_stage") == "offer",
        f"cand_00042 pipeline_stage={c42.get('data', {}).get('pipeline_stage')}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    c17 = by_id.get("cand_00017", {})
    ctx.check(
        "incremental_applied_tombstone",
        c17.get("is_deleted") is True,
        f"cand_00017 is_deleted={c17.get('is_deleted')}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )
    ctx.check(
        "incremental_applied_create",
        "cand_09000" in by_id,
        "cand_09000 was never inserted by the incremental poll",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # ------------------------------------------------------------------ L3
    log = vendor.request_log()
    pages = _candidate_pages(log)

    first_fault_ts = next(
        (e.get("ts", 0) for e in pages
         if _offset(e) == _FAULT_OFFSET and int(e.get("status", 0)) >= 500),
        None,
    )
    retried_ok = any(
        _offset(e) == _FAULT_OFFSET and int(e.get("status", 0)) == 200 for e in pages
    )
    ctx.check(
        "resume_not_restart",
        first_fault_ts is not None and retried_ok,
        f"fault_seen={first_fault_ts is not None} retried_ok={retried_ok} "
        f"statuses={[int(e.get('status', 0)) for e in pages][:8]}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    ids = [r["source_id"] for r in store]
    dup_ids = sorted({i for i in ids if ids.count(i) > 1})
    ctx.check(
        "exactly_once",
        not dup_ids and len(store) == len(fixture),
        f"dup_ids={dup_ids[:5]} store_rows={len(store)} fixture_rows={len(fixture)}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # NOT `retry_after_honored_l3` — that is backfill_under_faults.py's name for
    # a DIFFERENT assertion, and the same name must never be scored two ways
    # (the scorer collapses repeated names to one instance, worst case wins, so
    # a mismatch is silently resolved rather than reported).
    #
    # MEASURED: `rate_limited_hits=0` here for gold AND for the empty probe. The
    # CP1 recreate truncates the vendor's log, and the incremental result set is
    # ~3 candidates on a single page, so the 40-req/60s limiter cannot fire in
    # this phase at all. That makes `violations == 0` a prohibition satisfiable
    # by SILENCE — true of any connector that barely issues requests, including
    # one that does nothing. It was +2/mandatory under the name
    # `retry_after_honored_l3`, and the only reason the audit bar still passed is
    # that name dedupe collapsed it against the backfill scenario's genuine
    # failure. Scored 0/-1 and renamed: a real violation must cost, but there is
    # nothing here for a submission to earn.
    violations = logs.retry_after_violations(log)
    rate_limited_hits = [e for e in log if e.get("rate_limited")]
    ctx.check(
        "no_retry_after_violation_on_incremental",
        len(violations) == 0,
        f"rate_limited_hits={len(rate_limited_hits)} violations={len(violations)}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    # Incremental catch-up must not re-crawl the whole table. The provided
    # (correct) sync.py already builds this request with modified_since
    # regardless of client fault-handling, so an unmodified starter already
    # satisfies this — only a regression should cost.
    incr_pages = [e for e in pages if "modified_since" in (e.get("query") or {})]
    ctx.check(
        "incremental_used_modified_since",
        len(incr_pages) >= 1,
        "no GET /v1/candidates carried modified_since",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    await builtin_l2(ctx)

"""backfill_under_faults — full 6000-candidate backfill while offset 3000
returns 500 twice (FAULT_5XX_ON_PAGE="3000:2") AND a 40-req/60s rate limiter is
enabled (FAULT_RATE_LIMIT=1), both set on the vendor service in
docker-compose.yaml.

L1  : app exits 0; row count is 6000.
L3  : resume_not_restart — the faulted offset is retried to a 200 and NO
      strictly-earlier offset is re-fetched (200) after the first failure;
      exactly_once — no duplicate source_ids, count matches the fixture;
      retry_after_honored_l3 — the completed crawl either recovered from an
      observed 429 while honoring Retry-After, or proactively stayed below the
      configured request budget so the vendor correctly never served one.
L2  : builtin conduct gates/soft checks (creds, pagination hygiene, ...).
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

_FAULT_OFFSET = 3000


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


def _configured_rate_budget(ctx) -> tuple[int, float]:
    environment = (
        (((ctx.task.raw.get("contract") or {}).get("runtime") or {}).get("vendor_roles") or {})
        .get("globalhire", {})
        .get("environment", {})
    )
    try:
        limit = int(environment.get("GH_RATE_LIMIT", 40))
        window_s = float(environment.get("GH_RATE_WINDOW_S", 60))
    except (TypeError, ValueError):
        return 40, 60.0
    return limit, window_s


async def run(ctx) -> None:
    reset_store(ctx)
    ctx.vendor("globalhire").recreate(checkpoint=0)

    exit_code, _stdout, stderr = ctx.app.run(["sync"])

    store = dump_store(ctx)
    # AND-ed with store readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run. "Ran / output readable"
    # is exactly the pattern a do-nothing submission could win by executing —
    # pass=0, only a regression (crash / unreadable output) costs.
    ctx.check(
        "app_exit_ok",
        exit_code == 0 and store is not None,
        f"exit={exit_code} stderr={stderr[:500]} store_readable={store is not None}",
        pass_value=0,
        fail_value=-1,
        mandatory=False,
    )

    if store is None:
        ctx.check(
            "backfill_store_readable",
            False,
            "dump produced no output",
            pass_value=0,
            fail_value=-1,
            mandatory=False,
        )
        return

    fixture = load_fixture(ctx, "candidates.json")
    # Restores the signal of the deleted `backfill_matches_fixture` blob
    # compare, per row and per field. Without it this scenario graded the row
    # COUNT and the id multiset only — a connector that converged on 6,000 rows
    # of wrong data scored full marks. This is the "converge" half of the
    # task's mechanic; resume_not_restart below is the "resume" half.
    diffs = row_diff(store, fixture)
    ctx.check(
        "backfill_rows_exact",
        not diffs,
        diff_detail("candidates", store, fixture, diffs),
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )
    ctx.check(
        "backfill_row_count_6000",
        len(store) == 6000,
        f"store rows={len(store)} (expected 6000)",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # ------------------------------------------------------------------ L3
    log = ctx.vendor("globalhire").request_log()
    pages = _candidate_pages(log)

    # (a) resume, not restart.
    first_fault_ts = next(
        (e.get("ts", 0) for e in pages
         if _offset(e) == _FAULT_OFFSET and int(e.get("status", 0)) >= 500),
        None,
    )
    retried_ok = any(
        _offset(e) == _FAULT_OFFSET and int(e.get("status", 0)) == 200 for e in pages
    )
    refetched_earlier = []
    if first_fault_ts is not None:
        refetched_earlier = [
            _offset(e) for e in pages
            if e.get("ts", 0) > first_fault_ts
            and int(e.get("status", 0)) == 200
            and 0 <= _offset(e) < _FAULT_OFFSET
        ]
    ctx.check(
        "resume_not_restart",
        first_fault_ts is not None and retried_ok and not refetched_earlier,
        f"fault_seen={first_fault_ts is not None} retried_ok={retried_ok} "
        f"refetched_earlier_offsets={sorted(set(refetched_earlier))[:5]}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # (b) exactly once: no duplicate ids, count equals the fixture.
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

    # (c) retry_after_honored_l3 — two equally safe strategies are valid:
    # recover from a served 429 without retrying early, or pace the entire
    # completed crawl below the configured ceiling so no 429 is served.  The
    # old `>= 5` requirement made the second, safer strategy mathematically
    # incapable of passing: task-0022's Fable 5.1 submission completed all 61
    # pages while pacing at 22 requests/minute against the 25/minute ceiling,
    # then received zero credit precisely because it avoided every 429.
    #
    # Requiring a full crawl keeps this non-vacuous.  The do-nothing starter
    # stops around request 26 and cannot satisfy either route, while the exact
    # output and resume checks independently remain mandatory.
    _MIN_COMPLETED_CRAWL_REQUESTS = 61
    rate_limit, rate_window_s = _configured_rate_budget(ctx)
    rate_limited_hits = [e for e in log if e.get("rate_limited")]
    violations = logs.retry_after_violations(log)
    budget_violations = logs.request_budget_violations(
        pages, limit=rate_limit, window_s=rate_window_s
    )
    completed_crawl = len(pages) >= _MIN_COMPLETED_CRAWL_REQUESTS
    recovered_safely = completed_crawl and bool(rate_limited_hits) and not violations
    paced_safely = (
        completed_crawl
        and not rate_limited_hits
        and not budget_violations
    )
    ctx.check(
        "retry_after_honored_l3",
        recovered_safely or paced_safely,
        f"candidate_requests={len(pages)} rate_limited_hits={len(rate_limited_hits)} "
        f"retry_after_violations={len(violations)} "
        f"budget_violations={len(budget_violations)} "
        f"budget={rate_limit}/{rate_window_s:g}s",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    await builtin_l2(ctx)

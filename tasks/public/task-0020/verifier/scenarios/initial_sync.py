"""Scenario 1 — initial full backfill via offset pagination (exhaustion).

Flow:
  1. Recreate the vendor at checkpoint 0 and run ``globalhire-sync sync`` — the
     first pass with an empty store, so it back-fills EVERY candidate (6000 of
     them) by paging the candidate list to exhaustion.
  2. Snapshot the canonical store and assert it matches the cp0 answer key.

This bites all three stale-doc lies at once:
  - Termination (wrong_contract_edge): the wire response has NO ``has_more`` and
    NO ``total`` — the only terminal signal is a page shorter than ``limit`` (an
    empty page). A connector that waits for ``has_more: false`` either loops
    forever or stops after page 1 with 100 rows.
  - Field name (stale_field_name): the candidate's pipeline field is
    ``pipeline_stage`` on the wire (docs call it ``status``). A connector that
    maps ``status`` stores nothing for it.
  - Timestamp format (wrong_format): every ``modified_at`` carries a per-record
    numeric offset (never ``Z``). ``updated_at`` must be the TRUE UTC epoch
    second; a "strip the Z / assume UTC" parser is wrong by the offset amount.
A connector that trusts the docs infinite-loops / stops early, drops the pipeline
field, or mis-parses the timestamp — and the store won't match.

Three of the L1 checks below are guarded with `bool(store) and ...` (spec
§3c): the un-guarded predicates (`len(ids) == len(set(ids))`, `not
bad_stage`, `all(...)` over the store) are vacuously TRUE on an empty or
missing store, so a submission that never produces any rows must not bank
them for free.

Also asserts the split-brain (v2) regression guardrail (spec §3a): this task
stays pinned v1-only, so GH_V2_ENABLED must stay unset and no request may
ever target /v2/*.

Then run the built-in L2 gates (creds-in-query, pagination hygiene, ...).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from bench.verifier.builtin_l2 import builtin_l2

from _scenario_util import (  # noqa: E402
    assert_gh_v2_disabled,
    diff_detail,
    dump_store,
    load_fixture,
    reset_store,
    row_diff,
)

# Valid pipeline-stage enum values (wire field: pipeline_stage).
_STAGES = {"sourced", "screening", "submitted", "interview", "offer", "placed"}

# A known base record and its TRUE UTC last-modified instant (epoch SECONDS).
# cand_00001's wire modified_at is 2026-01-04T21:01:00-03:00, i.e. 2026-01-05T
# 00:01:00Z. A naive "assume UTC / strip offset" parser would instead compute
# 2026-01-04T21:01:00Z = 1767560460 (off by the -03:00 offset, 10800s), so this
# value is only reachable by honoring the numeric offset.
_KNOWN_ID = "cand_00001"
_KNOWN_UPDATED_AT_UTC = 1767571260   # true UTC epoch seconds
_KNOWN_UPDATED_AT_NAIVE = 1767560460  # what strip-Z / assume-UTC would produce


async def run(ctx) -> None:
    # -- 1. backfill at cp0 --------------------------------------------------
    reset_store(ctx)
    ctx.vendor("globalhire").recreate(checkpoint=0)

    code, _out, err = ctx.app.run(["sync"])

    # -- 2. store matches the cp0 answer key --------------------------------
    store = dump_store(ctx)
    # AND-ed with store readability (task-0043 pattern, 2026-08-02): exit 0
    # alone is vacuously bankable by a do-nothing run.
    # Pure plumbing: "the run happened and left a readable store". The three
    # stale-doc lies are graded below; this only costs when it breaks.
    ctx.check("backfill_exit_ok",
        code == 0 and store is not None,
        f"exit={code} stderr={err[:400]} store_readable={store is not None}",
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
    # Replaces the `store == cp0` blob compare (`initial_sync_matches_fixture`)
    # with a per-row, per-field diff. Over 6000 rows the blob compare's detail
    # was just "rows=N fixture rows=M", which cannot say WHICH of the three
    # stale-doc lies was tripped; the checks below isolate each lie and this one
    # is the whole-store verdict they roll up to.
    cp0 = load_fixture(ctx, "candidates_checkpoint_0.json")
    diffs = row_diff(store, cp0)
    ctx.check("initial_sync_rows_exact",
        not diffs,
        diff_detail("candidates@cp0", store, cp0, diffs),
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # The whole tenant list back-filled: exactly 6000 candidates. A connector
    # that stops on a missing `has_more` after the first page lands ~100 rows.
    # Lie 1 (wrong_contract_edge / termination): the wire has no `has_more` and
    # no `total`, so the only terminal signal is a short page. A docs-following
    # connector stops after page 1 with ~100 rows or never terminates. A
    # submission that silently loses 5900 rows must not be Solved.
    ctx.check("backfill_row_count_6000",
        len(store) == 6000,
        f"store rows={len(store)} (expected 6000; a stop-early paging bug loses rows)",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # The store is keyed exactly once per candidate — no missing rows, no dupes.
    # `bool(store) and ...` guards against the vacuous-pass loophole (spec
    # §3c): `len(ids) == len(set(ids))` is 0 == 0 (trivially True) on an
    # empty/missing store, so a submission that never produces any rows must
    # not bank this check for free.
    ids = [r["source_id"] for r in store]
    ctx.check("backfill_no_duplicate_rows",
        bool(store) and len(ids) == len(set(ids)),
        f"rows={len(ids)} distinct={len(set(ids))}",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # stale_field_name: the pipeline field is `pipeline_stage` (docs say
    # `status`). Every row's data must carry a valid pipeline_stage — a
    # connector that mapped `status` would leave it absent/None for every row.
    # `bool(store) and ...` guard: `not bad_stage` is vacuously True (`not
    # []`) on an empty store, per spec §3c.
    bad_stage = [
        r["source_id"] for r in store
        if r.get("data", {}).get("pipeline_stage") not in _STAGES
    ]
    # Lie 2 (stale_field_name): the wire field is `pipeline_stage`, the docs say
    # `status`. A docs-following connector stores nothing for it on every row.
    ctx.check("backfill_pipeline_stage_present",
        bool(store) and not bad_stage,
        f"{len(bad_stage)} row(s) missing a valid pipeline_stage; sample={bad_stage[:5]}",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # wrong_format: updated_at is the TRUE UTC epoch second, parsed by honoring
    # the per-record numeric offset. A strip-Z / assume-UTC parser lands the
    # naive value instead. Assert both: the correct value is present AND it is
    # not the naive one.
    known = next((r for r in store if r["source_id"] == _KNOWN_ID), None)
    # Lie 3 (wrong_format): every modified_at carries a per-record numeric
    # offset and never a Z. A strip-Z / assume-UTC parser lands the naive value,
    # wrong by the offset. This check names both values so the failure is
    # unambiguous, and it is the one place the lie is isolated from the rest.
    ctx.check("backfill_updated_at_is_true_utc",
        known is not None
        and known.get("updated_at") == _KNOWN_UPDATED_AT_UTC
        and known.get("updated_at") != _KNOWN_UPDATED_AT_NAIVE,
        f"{_KNOWN_ID} updated_at={None if known is None else known.get('updated_at')} "
        f"(expected {_KNOWN_UPDATED_AT_UTC}; naive strip-Z would give {_KNOWN_UPDATED_AT_NAIVE})",
        pass_value=2,
        fail_value=0,
        mandatory=True,
    )

    # Every updated_at is an int (a mis-parse would drop it or make it a
    # string). `bool(store) and ...` guard: `all(...)` over an empty iterable
    # is vacuously True, per spec §3c.
    ctx.check("backfill_updated_at_is_int",
        bool(store) and all(isinstance(r.get("updated_at"), int) for r in store),
        "some rows have a non-integer updated_at (timestamp mis-parsed)",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # The crawl actually paged to exhaustion (6000 candidates / 100 per page =>
    # ~60 pages) and terminated: if it had never terminated the run would have
    # timed out, so reaching here with a matching 6000-row store already proves
    # clean termination on the empty page.
    request_log = ctx.vendor("globalhire").request_log()
    candidate_pages = [
        e for e in request_log
        if e.get("path") == "/v1/candidates" and e.get("method") == "GET"
    ]
    ctx.check("backfill_paged_to_exhaustion",
        len(candidate_pages) >= 60,
        f"only {len(candidate_pages)} candidate list request(s); expected >= 60 pages",
        pass_value=1,
        fail_value=0,
        mandatory=False,
    )

    # Regression guardrail (spec §3a): this task stays v1-only.
    assert_gh_v2_disabled(ctx, request_log, label="initial_sync")

    await builtin_l2(ctx)
